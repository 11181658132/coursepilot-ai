import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import Settings, get_settings
from app.main import app, get_knowledge_base
from app.models import SyncReport
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.index_jobs import (
    IndexBusyError,
    JobStore,
    index_writer,
    queue_path,
    queue_scope,
)
from app.worker import run_once
from fastapi.testclient import TestClient


class EmbeddingClient:
    def __init__(self):
        self.calls = 0
        self.fail = None
        self.after_embed = None
        self.embeddings = SimpleNamespace(create=self.embed)

    def embed(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise self.fail
        if self.after_embed:
            self.after_embed()
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[1.0, 0.0])
                  for i, _ in enumerate(kwargs["input"])],
            usage=SimpleNamespace(total_tokens=100),
        )


class TemporaryProviderError(RuntimeError):
    status_code = 429
    code = "rate_limit_exceeded"
    response = SimpleNamespace(headers={"retry-after": "90"})


class QuotaError(TemporaryProviderError):
    code = "insufficient_quota"


class IndexJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = Settings(_env_file=None, knowledge_base_dir=root / "knowledge",
                               rag_db_path=root / "rag.sqlite3",
                               telemetry_path=root / "events.jsonl",
                               conversation_db_path=root / "chat.db",
                               index_state_path=root / "state.json", rag_engine="hybrid")
        self.path = self.config.knowledge_base_dir / "A/lecture/week1.md"
        self.path.parent.mkdir(parents=True)
        self.path.write_text("Database transactions preserve consistency.")
        self.store = JobStore(queue_path(self.config))
        self.scope = queue_scope(self.config)
        self.client = EmbeddingClient()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def factory(self, config):
        return HybridKnowledgeBase(config, self.client)

    def api(self):
        app.dependency_overrides[get_settings] = lambda: self.config
        app.dependency_overrides[get_knowledge_base] = lambda: SimpleNamespace()
        return TestClient(app)

    def test_queue_survives_reopening_database(self):
        job = self.store.enqueue(self.scope, "request-1")
        reopened = JobStore(queue_path(self.config))
        self.assertEqual(reopened.get(job["id"], self.scope)["status"], "queued")
        self.assertEqual(self.client.calls, 0)

    def test_concurrent_enqueue_has_one_active_job(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(lambda _: self.store.enqueue(self.scope)["id"], range(12)))
        self.assertEqual(len(set(ids)), 1)

    def test_upload_during_scan_creates_one_followup_and_never_misses_new_file(self):
        initial = self.store.enqueue(self.scope)
        followups = []

        def after_embed():
            self.client.after_embed = None
            self.path.with_name("late.md").write_text("Late uploaded database evidence.")
            with ThreadPoolExecutor(max_workers=4) as pool:
                followups.extend(pool.map(lambda _: self.store.enqueue(
                    self.scope, after_running=True)["id"], range(4)))

        self.client.after_embed = after_embed
        run_once(self.config, self.factory)
        self.assertEqual(self.store.get(initial["id"], self.scope)["report"]["uploaded"], 1)
        self.assertEqual(len(set(followups)), 1)
        self.assertNotEqual(followups[0], initial["id"])
        run_once(self.config, self.factory)
        self.assertEqual(self.store.get(followups[0], self.scope)["report"]["uploaded"], 1)
        self.assertEqual(self.client.calls, 2)
        self.assertEqual(len(self.factory(self.config).manifest()), 2)

    def test_crash_recovery_keeps_followup_queued_without_unique_constraint_failure(self):
        old = self.store.enqueue(self.scope)
        with index_writer(self.config):
            self.store.claim(self.scope)
        followup = self.store.enqueue(self.scope, after_running=True)
        run_once(self.config, self.factory)
        self.assertEqual(self.store.get(old["id"], self.scope)["status"], "succeeded")
        self.assertEqual(self.store.get(followup["id"], self.scope)["status"], "queued")
        run_once(self.config, self.factory)
        self.assertEqual(self.client.calls, 1)

    def test_followup_cannot_bypass_scope_retry_after(self):
        self.store.enqueue(self.scope)
        waiting = []

        def fail(**kwargs):
            waiting.append(self.store.enqueue(self.scope, after_running=True))
            raise TemporaryProviderError("Do not expose provider details")

        self.client.embeddings.create = fail
        before = time.time()
        run_once(self.config, self.factory)
        self.assertGreaterEqual(self.store.get(waiting[0]["id"], self.scope)["available_at"],
                                before + 90)
        self.assertFalse(run_once(self.config, self.factory))

    def test_followup_stops_on_billing_problem_and_requires_manual_retry(self):
        self.store.enqueue(self.scope)
        waiting = []

        def fail(**kwargs):
            waiting.append(self.store.enqueue(self.scope, after_running=True))
            raise QuotaError("Private billing details")

        self.client.embeddings.create = fail
        run_once(self.config, self.factory)
        state = self.store.get(waiting[0]["id"], self.scope)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error_code"], "provider_quota_exhausted")
        self.assertFalse(run_once(self.config, self.factory))

    def test_successful_upload_key_can_rebuild_deleted_index_without_reembedding_unchanged(self):
        first = self.store.enqueue(self.scope, "upload:hash", after_running=True)
        run_once(self.config, self.factory)
        second = self.store.enqueue(self.scope, "upload:hash", after_running=True)
        self.assertNotEqual(first["id"], second["id"])
        run_once(self.config, self.factory)
        self.assertEqual(self.client.calls, 1)
        self.factory(self.config).store.delete_document("A/lecture/week1.md")
        third = self.store.enqueue(self.scope, "upload:hash", after_running=True)
        run_once(self.config, self.factory)
        self.assertEqual(self.client.calls, 2)
        self.assertEqual(self.store.get(third["id"], self.scope)["status"], "succeeded")

    def test_failed_upload_key_does_not_reset_attempts_or_silently_retry(self):
        first = self.store.enqueue(self.scope, "upload:failed", after_running=True)
        self.client.fail = QuotaError("private")
        run_once(self.config, self.factory)
        again = self.store.enqueue(self.scope, "upload:failed", after_running=True)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(again["status"], "failed")
        self.assertEqual(again["attempts"], 1)

    def test_v1_queue_migration_preserves_jobs_and_request_keys(self):
        job = self.store.enqueue(self.scope, "preserved")
        with self.store.connect() as db:
            db.execute("DROP INDEX one_running_index_job")
            db.execute("CREATE UNIQUE INDEX one_active_index_job ON index_jobs(scope) "
                       "WHERE status IN ('queued','running')")
            db.execute("PRAGMA user_version=1")
        migrated = JobStore(self.store.path)
        self.assertEqual(migrated.enqueue(self.scope, "preserved")["id"], job["id"])
        with migrated.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        with index_writer(self.config):
            migrated.claim(self.scope)
        followup = migrated.enqueue(self.scope, after_running=True)
        self.assertNotEqual(followup["id"], job["id"])

    def test_manual_retry_still_rejects_another_active_job(self):
        job = self.store.enqueue(self.scope)
        with index_writer(self.config):
            claimed = self.store.claim(self.scope)
        self.store.finish(claimed, error_code="synthetic")
        self.store.enqueue(self.scope)
        with self.assertRaises(IndexBusyError):
            self.store.retry(job["id"], self.scope)

    def test_idempotency_key_returns_finished_job_even_after_new_enqueue(self):
        old = self.store.enqueue(self.scope, "once")
        run_once(self.config, self.factory)
        new = self.store.enqueue(self.scope)
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(self.store.enqueue(self.scope, "once")["id"], old["id"])

    def test_successful_job_and_unchanged_repeat_do_not_embed_twice(self):
        job = self.store.enqueue(self.scope)
        self.assertTrue(run_once(self.config, self.factory))
        result = self.store.get(job["id"], self.scope)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["processed"], result["total"])
        self.assertEqual(result["report"]["uploaded"], 1)
        self.assertGreater(result["estimated_cost_usd"], 0)
        self.store.enqueue(self.scope)
        run_once(self.config, self.factory)
        self.assertEqual(self.client.calls, 1)

    def test_dead_worker_is_recovered_and_attempt_is_incremented(self):
        job = self.store.enqueue(self.scope)
        with index_writer(self.config):
            self.assertEqual(self.store.claim(self.scope)["attempts"], 1)
        # Simulates process death after claim, before publishing a result.
        run_once(self.config, self.factory)
        result = self.store.get(job["id"], self.scope)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempts"], 2)

    def test_actual_process_death_releases_lock_and_recovers_committed_claim(self):
        job = self.store.enqueue(self.scope)
        script = """
import os, sys
from pathlib import Path
from app.config import Settings
from app.services.index_jobs import JobStore, index_writer, queue_path, queue_scope
config = Settings(_env_file=None, knowledge_base_dir=Path(sys.argv[1]),
                  rag_db_path=Path(sys.argv[2]))
with index_writer(config):
    JobStore(queue_path(config)).claim(queue_scope(config))
    os._exit(17)
"""
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        process = subprocess.run([sys.executable, "-c", script,
                                  str(self.config.knowledge_base_dir),
                                  str(self.config.rag_db_path)], env=environment,
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 17, process.stderr.decode())
        self.assertEqual(self.store.get(job["id"], self.scope)["status"], "running")
        run_once(self.config, self.factory)
        result = self.store.get(job["id"], self.scope)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempts"], 2)

    def test_crash_after_document_commit_resumes_without_reembedding(self):
        job = self.store.enqueue(self.scope)

        def interrupted_factory(config):
            base = self.factory(config)

            def sync(root, progress):
                base.sync(root)
                raise SystemExit("Simulated process termination after document commit")

            return SimpleNamespace(sync=sync)

        with self.assertRaises(SystemExit):
            run_once(self.config, interrupted_factory)
        self.assertEqual(self.store.get(job["id"], self.scope)["status"], "running")
        run_once(self.config, self.factory)
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(self.store.get(job["id"], self.scope)["report"]["skipped"], 1)

    def test_max_crash_attempts_are_not_replayed_forever(self):
        job = self.store.enqueue(self.scope)
        with index_writer(self.config):
            for _ in range(3):
                self.assertIsNotNone(self.store.claim(self.scope))
        self.assertFalse(run_once(self.config, self.factory))
        self.assertEqual(self.store.get(job["id"], self.scope)["status"], "failed")
        self.assertEqual(self.client.calls, 0)

    def test_second_writer_cannot_run_or_recover_an_active_job(self):
        job = self.store.enqueue(self.scope)
        with index_writer(self.config):
            self.store.claim(self.scope)
            self.assertFalse(run_once(self.config, self.factory))
            with self.assertRaises(IndexBusyError), index_writer(self.config):
                pass
        self.assertEqual(self.store.get(job["id"], self.scope)["attempts"], 1)

    def test_retry_after_is_persisted_and_not_slept_in_worker(self):
        job = self.store.enqueue(self.scope)
        self.client.fail = TemporaryProviderError("SECRET provider response")
        before = time.time()
        run_once(self.config, self.factory)
        result = self.store.get(job["id"], self.scope)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["phase"], "retry_wait")
        self.assertGreaterEqual(result["available_at"], before + 90)
        self.assertFalse(run_once(self.config, self.factory))
        self.assertEqual(self.client.calls, 1)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_quota_problem_does_not_auto_retry(self):
        job = self.store.enqueue(self.scope)
        self.client.fail = QuotaError("billing details")
        run_once(self.config, self.factory)
        self.assertEqual(self.store.get(job["id"], self.scope)["status"], "failed")
        self.assertEqual(self.store.get(job["id"], self.scope)["error_code"],
                         "provider_quota_exhausted")
        self.assertFalse(run_once(self.config, self.factory))

    def test_manual_retry_preserves_successful_document_work(self):
        second = self.path.with_name("week2.md")
        second.write_text("")
        job = self.store.enqueue(self.scope)
        run_once(self.config, self.factory)
        failed = self.store.get(job["id"], self.scope)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["report"]["uploaded"], 1)
        first_cost = failed["estimated_cost_usd"]
        second.unlink()
        self.store.retry(job["id"], self.scope)
        run_once(self.config, self.factory)
        succeeded = self.store.get(job["id"], self.scope)
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertEqual(succeeded["report"]["skipped"], 1)
        self.assertEqual(succeeded["estimated_cost_usd"], first_cost)
        self.assertEqual(self.client.calls, 1)

    def test_incomplete_replacement_keeps_previous_index(self):
        base = self.factory(self.config)
        base.sync(self.config.knowledge_base_dir)
        previous = base.manifest()
        self.path.write_text("Database replacement")
        self.client.fail = RuntimeError("secret")
        job = self.store.enqueue(self.scope)
        run_once(self.config, self.factory)
        self.assertEqual(base.manifest(), previous)
        self.assertEqual(self.store.get(job["id"], self.scope)["status"], "failed")

    def test_database_write_failure_rolls_back_deleted_old_chunks(self):
        base = self.factory(self.config)
        base.sync(self.config.knowledge_base_dir)
        previous = base.manifest()
        old_chunks = base.store.rows(base.signature, "A", "all")
        self.path.write_text("Database replacement content")
        with patch("app.services.chunk_store.json.dumps", side_effect=RuntimeError("failed")):
            report = base.sync(self.config.knowledge_base_dir)
        self.assertEqual(report.failed, 1)
        self.assertEqual(base.manifest(), previous)
        self.assertEqual(base.store.rows(base.signature, "A", "all"), old_chunks)

    def test_source_change_during_embedding_cannot_publish_wrong_hash(self):
        base = self.factory(self.config)
        self.client.after_embed = lambda: self.path.write_text("Changed during provider call")
        report = base.sync(self.config.knowledge_base_dir)
        self.assertEqual(report.failed, 1)
        self.assertEqual(base.manifest(), {})

    def test_api_submission_is_fast_and_contains_no_model_calls(self):
        api = self.api()
        response = api.post("/api/index-jobs", headers={"Idempotency-Key": "button-click"})
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertNotIn("scope", job)
        self.assertEqual(job["attempts"], 0)
        self.assertEqual(api.get(f"/api/index-jobs/{job['id']}").status_code, 200)
        self.assertEqual(len(api.get("/api/index-jobs").json()), 1)
        self.assertEqual(api.post("/api/index-jobs", headers={
            "Idempotency-Key": "button-click"}).json()["id"], job["id"])
        self.assertEqual(self.client.calls, 0)

    def test_api_retry_requires_failed_job_and_known_id(self):
        api = self.api()
        job = api.post("/api/index-jobs").json()
        self.assertEqual(api.post(f"/api/index-jobs/{job['id']}/retry").status_code, 409)
        self.assertEqual(api.post("/api/index-jobs/unknown/retry").status_code, 404)
        self.assertEqual(api.get("/api/index-jobs/unknown").status_code, 404)
        self.assertEqual(api.post("/api/index-jobs?engine=file_search").status_code, 422)

    def test_sync_and_delete_fail_fast_while_writer_active(self):
        api = self.api()
        with index_writer(self.config):
            response = api.post("/api/sync?engine=hybrid")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.headers["retry-after"], "2")
            response = api.request("DELETE", "/api/documents", json={
                "path": "A/lecture/week1.md"})
            self.assertEqual(response.status_code, 409)
        self.assertTrue(self.path.is_file())

    def test_other_config_scope_cannot_see_or_retry_jobs(self):
        job = self.store.enqueue("different-scope")
        api = self.api()
        self.assertEqual(api.get(f"/api/index-jobs/{job['id']}").status_code, 404)
        self.assertEqual(api.post(f"/api/index-jobs/{job['id']}/retry").status_code, 404)

    def test_configuration_change_changes_job_namespace(self):
        other = self.config.model_copy(update={"rag_chunk_chars": 500})
        self.assertNotEqual(queue_scope(other), self.scope)

    def test_job_exception_has_no_provider_details(self):
        job = self.store.enqueue(self.scope)
        with patch("app.worker.HybridKnowledgeBase"):
            run_once(self.config, lambda _: (_ for _ in ()).throw(RuntimeError("API_KEY=secret")))
        result = self.store.get(job["id"], self.scope)
        self.assertEqual(result["error_code"], "RuntimeError")
        self.assertNotIn("secret", json.dumps(result))

    def test_progress_is_visible_before_job_finishes(self):
        job = self.store.enqueue(self.scope)
        observed = []

        def fake(config):
            def sync(root, progress):
                progress(1, 2, "embedding")
                observed.append(self.store.get(job["id"], self.scope))
                return SyncReport(uploaded=2)
            return SimpleNamespace(sync=sync)

        run_once(self.config, fake)
        self.assertEqual(observed[0]["status"], "running")
        self.assertEqual(observed[0]["phase"], "embedding")
        self.assertEqual(observed[0]["processed"], 1)

    def test_real_pipeline_reports_parsing_chunking_embedding_and_atomic_commit(self):
        phases = []
        self.factory(self.config).sync(self.config.knowledge_base_dir,
                                      progress=lambda done, total, phase: phases.append(phase))
        expected = ["parsing", "chunking", "embedding", "committing"]
        positions = [phases.index(phase) for phase in expected]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()

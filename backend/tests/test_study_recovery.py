import json
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.config import Settings, get_settings
from app.main import app, get_study_store
from app.services.study_agent import MeteredClient, PlanRequest, StudyAgent
from app.services.study_feedback import FeedbackAgent, FeedbackRequest
from app.services.study_store import StudyConflict, StudyStore
from fastapi.testclient import TestClient


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StudyStore(Path(self.temp.name) / "plans.sqlite3")
        self.request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", goal="Review RAG")
        self.state, _ = self.store.start(self.request)
        self.run_id = self.state["id"]
        self.config = Settings(_env_file=None, openai_api_key="test-only")

    def expired(self, state=None):
        return patch("app.services.study_store.time.time", return_value=
                     (state or self.state)["lease_expires_at"] + 1)

    def active_plan(self):
        task = {"id": uuid.uuid4().hex, "day": 1, "done": False, "title": "Review"}
        self.store.finish(self.run_id, "draft", {"request": self.state["request"],
                                                "trace": [], "tasks": [task]})
        return self.store.confirm(self.run_id, 1), task

    def test_live_run_cannot_be_cancelled_by_recovery(self):
        before = self.store.get(self.run_id)
        after = self.store.recover(self.run_id, 0)
        self.assertEqual(before, after)
        self.assertFalse(after["recovery_available"])

    def test_checkpoint_is_durable_and_renews_lease_only_for_live_run(self):
        payload = self.store.payload(self.state)
        payload["trace"] = [{"step": 1, "tool": "list_materials", "status": "succeeded"}]
        with patch("app.services.study_store.time.time",
                   return_value=self.state["lease_expires_at"] - 1):
            self.store.checkpoint(self.run_id, payload)
        reopened = StudyStore(self.store.path).get(self.run_id)
        self.assertEqual(reopened["trace"], payload["trace"])
        self.assertGreater(reopened["lease_expires_at"], self.state["lease_expires_at"])
        self.assertNotIn("accounted_cost_usd", self.store.payload(reopened))
        self.assertNotIn("recovery_available", self.store.payload(reopened))

    def test_expired_run_preserves_trace_and_unknown_cost_and_cannot_be_confirmed(self):
        payload = self.store.payload(self.state)
        payload["trace"] = [{"step": 1, "tool": "list_materials", "status": "succeeded"},
                            {"step": 2, "tool": "search_knowledge", "status": "running"}]
        self.store.checkpoint(self.run_id, payload)
        self.store.reserve(self.run_id, 0.02, 0.05, 0.5)
        latest = self.store.get(self.run_id)
        with self.expired(latest):
            self.assertTrue(self.store.get(self.run_id)["recovery_available"])
            result = self.store.recover(self.run_id, 0)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["trace"][0]["status"], "succeeded")
        self.assertEqual(result["trace"][1]["status"], "interrupted")
        self.assertEqual(result["accounted_cost_usd"], 0.02)
        self.assertEqual(result["uncertain_calls"], 1)
        with self.assertRaises(StudyConflict):
            self.store.confirm(self.run_id, result["version"])

    def test_expired_or_interrupted_worker_cannot_reserve_checkpoint_or_publish(self):
        sdk = Mock()
        meter = MeteredClient(sdk, self.store, self.run_id, self.config)
        with self.expired():
            for operation in (lambda: meter.respond(model="gpt-6-luna"),
                              lambda: self.store.checkpoint(self.run_id, {}),
                              lambda: self.store.finish(self.run_id, "draft", {})):
                with self.assertRaises(StudyConflict):
                    operation()
            self.store.recover(self.run_id, 0)
        with self.assertRaises(StudyConflict):
            self.store.finish(self.run_id, "failed", {})
        sdk.responses.create.assert_not_called()
        self.assertEqual(self.store.get(self.run_id)["api_calls"], 0)

    def test_late_usage_can_settle_but_cannot_restore_an_interrupted_draft(self):
        charge = self.store.reserve(self.run_id, 0.02, 0.05, 0.5)
        with self.expired(self.store.get(self.run_id)):
            self.store.recover(self.run_id, 0)
        self.store.settle(charge, 0.0001)
        result = self.store.get(self.run_id)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["uncertain_calls"], 0)
        self.assertEqual(result["accounted_cost_usd"], 0.0001)

    def test_concurrent_recovery_and_old_request_replay_are_idempotent(self):
        with self.expired():
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.store.recover(self.run_id, 0), range(2)))
        self.assertEqual([r["version"] for r in results], [1, 1])
        agent = StudyAgent(self.config, self.store, client=Mock(), base=Mock())
        self.assertEqual(agent.run(self.request)["status"], "interrupted")
        agent.client.responses.create.assert_not_called()

    def test_legacy_run_without_lease_can_be_recovered_without_schema_changes(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
        with self.store.connect() as db:
            db.execute("UPDATE study_runs SET payload=?, created_at=? WHERE id=?",
                       (json.dumps({"request": self.state["request"], "trace": [], "tasks": []}),
                        old, self.run_id))
        self.assertTrue(self.store.get(self.run_id)["recovery_available"])
        self.assertEqual(self.store.recover(self.run_id, 0)["status"], "interrupted")

    def test_feedback_recovery_preserves_active_plan_and_progress(self):
        parent, task = self.active_plan()
        request = FeedbackRequest(request_id=uuid.uuid4(), version=parent["version"],
                                  answer="My fictional answer")
        child, _ = self.store.begin_feedback(self.run_id, task["id"], request)
        self.store.reserve(child["id"], 0.02, 0.05, 0.5)
        child = self.store.get(child["id"])
        parent = self.store.get(self.run_id)
        with self.expired(child):
            self.assertTrue(self.store.get(self.run_id)["recovery_available"])
            with self.assertRaises(StudyConflict):
                self.store.recover(self.run_id, parent["version"] - 1)
            result = self.store.recover(self.run_id, parent["version"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["tasks"], [task])
        self.assertEqual(result["version"], parent["version"] + 1)
        self.assertEqual(result["feedback"][0]["status"], "interrupted")
        self.assertEqual(result["feedback"][0]["uncertain_calls"], 1)
        with self.assertRaises(KeyError):
            self.store.recover(child["id"], 0)
        agent = FeedbackAgent(self.config, self.store, client=Mock())
        agent.run(self.run_id, task["id"], request)
        agent.client.responses.create.assert_not_called()

    def test_process_death_keeps_completed_tool_checkpoint(self):
        # BaseException simulates process death, bypassing ordinary exception cleanup.
        result = SimpleNamespace(status="completed", usage=SimpleNamespace(
            input_tokens=10, output_tokens=10), output=[SimpleNamespace(
                type="function_call", name="list_materials", call_id="one", arguments="{}")])
        sdk = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[
            result, KeyboardInterrupt()])))
        base = SimpleNamespace(manifest=lambda: {"source.md": {"course": "DEMO101"}})
        request = self.request.model_copy(update={"request_id": uuid.uuid4()})
        with self.assertRaises(KeyboardInterrupt):
            StudyAgent(self.config, self.store, client=sdk, base=base).run(request)
        state = self.store.list("DEMO101")[0]
        self.assertEqual(state["trace"][0]["status"], "succeeded")
        self.assertEqual(state["uncertain_calls"], 1)
        self.assertEqual(state["api_calls"], 2)
        with self.expired(state):
            recovered = self.store.recover(state["id"], state["version"])
        self.assertEqual(recovered["trace"], state["trace"])
        self.assertEqual(recovered["accounted_cost_usd"], state["accounted_cost_usd"])

    def test_api_requires_explicit_confirmation_and_returns_no_model_recovery(self):
        app.dependency_overrides[get_settings] = lambda: self.config
        app.dependency_overrides[get_study_store] = lambda: self.store
        self.addCleanup(app.dependency_overrides.clear)
        client = TestClient(app)
        route = f"/api/study-plans/{self.run_id}/recover"
        self.assertEqual(client.post(route, json={"confirmed": False, "version": 0}).status_code,
                         422)
        with self.expired():
            response = client.post(route, json={"confirmed": True, "version": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "interrupted")

    def test_late_model_response_cannot_continue_tools_or_publish(self):
        clock = [time.time()]
        count = [0]

        def respond(**kwargs):
            count[0] += 1
            if count[0] == 2:
                clock[0] += 181
            return SimpleNamespace(status="completed", usage=SimpleNamespace(
                input_tokens=10, output_tokens=10), output=[SimpleNamespace(
                    type="function_call", name="list_materials", call_id="one", arguments="{}")])

        sdk = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=respond)))
        base = SimpleNamespace(manifest=lambda: {"source.md": {"course": "DEMO101"}})
        with patch("app.services.study_store.time.time", side_effect=lambda: clock[0]):
            result = StudyAgent(self.config, self.store, client=sdk, base=base).run(
                self.request.model_copy(update={"request_id": uuid.uuid4()}))
            self.assertEqual(result["status"], "running")
            self.assertTrue(result["recovery_available"])
            self.assertEqual(len(result["trace"]), 1)
            recovered = self.store.recover(result["id"], result["version"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(recovered["uncertain_calls"], 0)  # Actual returned usage was settled.
        self.assertEqual(sdk.responses.create.call_count, 2)

    def test_feedback_process_death_keeps_search_checkpoint_without_changing_tasks(self):
        parent, task = self.active_plan()
        result = SimpleNamespace(status="completed", usage=SimpleNamespace(
            input_tokens=10, output_tokens=10), output=[SimpleNamespace(type="function_call",
                name="search_knowledge", call_id="one", arguments='{"query":"RAG"}')])
        sdk = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[
            result, KeyboardInterrupt()])))
        base = SimpleNamespace(retrieve=lambda *args: [{"filename": "source.md",
            "chunk_id": "fictional", "text": "Retrieved evidence should support claims."}])
        request = FeedbackRequest(request_id=uuid.uuid4(), version=parent["version"],
                                  answer="My fictional answer")
        with self.assertRaises(KeyboardInterrupt):
            FeedbackAgent(self.config, self.store, client=sdk, base=base).run(
                self.run_id, task["id"], request)
        saved = self.store.get(self.run_id)
        child = saved["feedback"][0]
        self.assertEqual(child["trace"][0]["status"], "succeeded")
        self.assertEqual(child["uncertain_calls"], 1)
        with self.expired(child):
            recovered = self.store.recover(self.run_id, saved["version"])
        self.assertEqual(recovered["status"], "active")
        self.assertEqual(recovered["tasks"], [task])
        self.assertEqual(recovered["feedback"][0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()

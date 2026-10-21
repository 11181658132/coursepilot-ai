"""Real PostgreSQL integration; only against an explicit disposable loopback test DB."""

import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from app.services.public_rag_limits import PublicRagLimits
from app.services.public_study_store import PgConnection, PublicStudyStore
from app.services.study_agent import PlanRequest
from app.services.study_feedback import FeedbackRequest
from app.services.study_store import StudyBudgetExceeded, StudyConflict

DSN = os.environ.get("COURSEPILOT_TEST_DATABASE_URL", "")


class IntegrationStore(PublicStudyStore):
    def __init__(self, dsn, tenant, quota, schema):
        self.schema = schema
        super().__init__(dsn, tenant, quota)

    @contextmanager
    def connect(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        # Only this test adapter allows plaintext to a guarded loopback disposable DB.
        with psycopg.connect(self.dsn, row_factory=dict_row, prepare_threshold=None) as db:
            db.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            yield PgConnection(db)


@unittest.skipUnless(DSN, "PostgreSQL integration requires disposable test database")
class PublicPostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql

        target = urlparse(DSN)
        if target.hostname not in {"localhost", "127.0.0.1"} or target.path != "/coursepilot_test":
            raise ValueError("Never run destructive fixtures against a non-test/non-loopback DB")
        self.schema = "test_cp_" + uuid.uuid4().hex
        with psycopg.connect(DSN) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            db.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            migration = Path(__file__).resolve().parents[2] / "frontend/sql/001-public-agent.sql"
            db.execute(migration.read_text())
        self.one = IntegrationStore(DSN, "a" * 64, "1" * 64, self.schema)
        self.two = IntegrationStore(DSN, "b" * 64, "2" * 64, self.schema)
        self.request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", goal="Review RAG")

    def tearDown(self):
        import psycopg
        from psycopg import sql

        # Remove only the random schema created by this test, never user/public tables.
        with psycopg.connect(DSN) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_migration_persistence_isolation_and_cost_aliases(self):
        first, _ = self.one.start(self.request)
        second, _ = self.two.start(self.request)
        self.assertNotEqual(first["id"], second["id"])
        charge = self.one.reserve(first["id"], 0.02, 0.05, 0.10)
        self.one.settle(charge, 0.0001234)
        snapshot = self.one.get(first["id"])
        self.assertAlmostEqual(snapshot["accounted_cost_usd"], 0.0001234)
        self.assertEqual(snapshot["uncertain_calls"], 0)
        self.assertEqual(snapshot["api_calls"], 1)
        self.assertEqual([r["id"] for r in self.one.list("DEMO101")], [first["id"]])
        with self.assertRaises(KeyError):
            self.two.get(first["id"])
        self.one.finish(first["id"], "draft", {"request": first["request"], "trace": [],
                                               "tasks": [{"id": "task", "done": False}]})
        active = self.one.confirm(first["id"], 1)
        self.one.set_task(first["id"], "task", True, active["version"])
        reopened = IntegrationStore(DSN, "a" * 64, "1" * 64, self.schema)
        self.assertTrue(reopened.get(first["id"])["tasks"][0]["done"])

    def test_advisory_lock_serializes_multi_connection_budget_reservations(self):
        first, _ = self.one.start(self.request)
        second, _ = self.two.start(self.request)

        def reserve(pair):
            store, run = pair
            try:
                store.reserve(run["id"], 0.02, 0.05, 0.03)
                return True
            except StudyBudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(reserve, [(self.one, first), (self.two, second)])), 1)

    def test_recovery_survives_new_connection_fences_worker_and_keeps_quota(self):
        run, _ = self.one.start(self.request)
        payload = self.one.payload(run)
        payload["trace"] = [{"step": 1, "tool": "search_knowledge", "status": "running"}]
        self.one.checkpoint(run["id"], payload)
        self.one.reserve(run["id"], 0.02, 0.05, 0.10)
        state = self.one.get(run["id"])
        with patch("app.services.study_store.time.time",
                   return_value=state["lease_expires_at"] + 1):
            with self.assertRaises(KeyError):
                self.two.recover(run["id"], state["version"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                recovered = list(pool.map(lambda _: self.one.recover(run["id"], 0), range(2)))
        self.assertEqual([item["version"] for item in recovered], [1, 1])
        reopened = IntegrationStore(DSN, "a" * 64, "1" * 64, self.schema)
        snapshot = reopened.get(run["id"])
        self.assertEqual(snapshot["status"], "interrupted")
        self.assertEqual(snapshot["uncertain_calls"], 1)
        self.assertEqual(snapshot["accounted_cost_usd"], 0.02)
        with self.assertRaises(StudyConflict):
            reopened.finish(run["id"], "draft", payload)
        _, fresh = reopened.start(self.request)
        self.assertFalse(fresh)
        reopened.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))
        with self.assertRaises(StudyBudgetExceeded):
            reopened.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))

    def rag_limits(self):
        limiter = PublicRagLimits(DSN)
        limiter.connect = self.one.connect  # Same disposable test schema and real PG adapter.
        return limiter

    def test_rag_daily_limit_is_atomic_and_survives_fresh_connections(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.rag_limits().quota("d" * 64, consume=True),
                                    range(8)))
        self.assertEqual(sum(item["allowed"] for item in results), 5)
        status = self.rag_limits().quota("d" * 64)
        self.assertFalse(status["allowed"])
        self.assertEqual(status["remaining_today"], 0)
        self.assertEqual(status["remaining_month"], 295)
        # Existing Agent quota remains independent in the shared table.
        self.one.start(self.request)

    def test_rag_monthly_limit_is_atomic_across_ips_and_denials_do_not_increment(self):
        from datetime import datetime, timezone

        month = datetime.now(timezone.utc).replace(day=1).date().isoformat()
        with self.one.connect() as db:
            db.execute("INSERT INTO public_demo_limits VALUES ('rag:global', ?, 299)", (month,))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: self.rag_limits().quota(f"{i:064x}", consume=True),
                                    range(4)))
        self.assertEqual(sum(item["allowed"] for item in results), 1)
        status = self.rag_limits().quota("f" * 64, consume=True)
        self.assertEqual(status["reason"], "monthly")
        self.assertEqual(status["remaining_today"], 5)
        self.assertEqual(status["remaining_month"], 0)

    def test_rotating_anonymous_visitor_preserves_ip_quota(self):
        self.one.start(self.request)
        changed = IntegrationStore(DSN, "c" * 64, "1" * 64, self.schema)
        changed.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))
        with self.assertRaises(StudyBudgetExceeded):
            changed.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))

    def test_feedback_confirmation_is_persistent_scoped_and_shares_existing_ip_quota(self):
        draft, _ = self.one.start(self.request)
        tasks = [{"id": uuid.uuid4().hex, "day": day, "title": "Initial task", "done": False,
                  "minutes": 45, "study_goal": "Review", "quiz_question": "Why cite?",
                  "quiz_answer": "Trace evidence.", "citations": []} for day in (1, 2)]
        self.one.finish(draft["id"], "draft", {"tasks": tasks, "request": draft["request"],
                                                "trace": []})
        plan = self.one.confirm(draft["id"], 1)
        request = FeedbackRequest(request_id=uuid.uuid4(), version=plan["version"],
                                  answer="Synthetic test answer.")
        feedback, fresh = self.one.begin_feedback(plan["id"], tasks[0]["id"], request)
        self.assertTrue(fresh)
        payload = self.one.payload(feedback)
        payload["assessment"] = {"verdict": "needs_review"}
        payload["adjustments"] = [{"task_id": tasks[1]["id"], "title": "Adjusted task",
            "study_goal": "Review evidence support", "quiz_question": "What supports a claim?",
            "quiz_answer": "Relevant evidence", "citations": []}]
        self.one.finish(feedback["id"], "draft", payload)
        with self.assertRaises(KeyError):
            self.two.confirm_feedback(plan["id"], feedback["id"], feedback["base_version"])
        updated = self.one.confirm_feedback(plan["id"], feedback["id"], feedback["base_version"])
        self.assertEqual(updated["tasks"][1]["title"], "Adjusted task")
        reopened = IntegrationStore(DSN, "a" * 64, "1" * 64, self.schema)
        self.assertEqual(reopened.get(plan["id"])["feedback"][0]["status"], "active")
        self.assertEqual(len(self.one.list("DEMO101")), 1)
        with self.assertRaises(StudyBudgetExceeded):
            self.one.begin_feedback(plan["id"], tasks[0]["id"], request.model_copy(update={
                "request_id": uuid.uuid4(), "version": updated["version"]}))


if __name__ == "__main__":
    unittest.main()

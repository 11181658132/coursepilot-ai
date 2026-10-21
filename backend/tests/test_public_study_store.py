import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from app.services.public_study_store import PgConnection, PgRow, PublicStudyStore
from app.services.study_agent import PlanRequest
from app.services.study_store import StudyBudgetExceeded, StudyStore


class SQLitePublicStore(PublicStudyStore):
    """Tests the shared state/isolation contract; not PostgreSQL compatibility."""

    connect = StudyStore.connect

    def __init__(self, path, tenant, quota_key):
        self.path = path
        super().__init__("test-only", tenant, quota_key, initialize=True)


class PublicStudyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "public.sqlite3"
        self.one = SQLitePublicStore(path, "a" * 64, "1" * 64)
        self.two = SQLitePublicStore(path, "b" * 64, "2" * 64)
        self.request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", goal="Review RAG")

    def tearDown(self):
        self.temporary.cleanup()

    def test_visitors_can_reuse_same_request_uuid_without_sharing_plans(self):
        first, _ = self.one.start(self.request)
        second, _ = self.two.start(self.request)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual([r["id"] for r in self.one.list("DEMO101")], [first["id"]])
        self.assertEqual([r["id"] for r in self.two.list("DEMO101")], [second["id"]])
        self.assertEqual(self.one.get(first["id"])["request"], first["request"])

    def test_every_cross_visitor_read_and_write_is_rejected_even_with_known_id(self):
        first, _ = self.one.start(self.request)
        charge = self.one.reserve(first["id"], 0.02, 0.05, 0.1)
        operations = [lambda: self.two.get(first["id"]),
                      lambda: self.two.finish(first["id"], "failed", {}),
                      lambda: self.two.confirm(first["id"], 0),
                      lambda: self.two.set_task(first["id"], "task", True, 0),
                      lambda: self.two.reserve(first["id"], 0.02, 0.05, 0.1),
                      lambda: self.two.settle(charge, 0)]
        for operation in operations:
            with self.assertRaises(KeyError):
                operation()
        self.assertEqual(self.one.get(first["id"])["status"], "running")

    def test_clearing_cookie_does_not_reset_ip_request_quota(self):
        same_ip = SQLitePublicStore(self.one.path, "c" * 64, "1" * 64)
        self.one.start(self.request)
        same_ip.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))
        with self.assertRaises(StudyBudgetExceeded):
            same_ip.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))

    def test_global_daily_cost_reservation_is_atomic_across_visitors(self):
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
            accepted = list(pool.map(reserve, [(self.one, first), (self.two, second)]))
        self.assertEqual(sum(accepted), 1)

    def test_monthly_budget_survives_new_visitors_and_processes(self):
        first, _ = self.one.start(self.request)
        with self.one.connect() as db:
            db.execute("INSERT INTO study_charges VALUES (?, ?, ?, ?, 0)",
                       (self.one.new_id(), first["id"], "9999-01-01", 0.49))
        second, _ = self.two.start(self.request)
        with self.assertRaises(StudyBudgetExceeded):
            self.two.reserve(second["id"], 0.02, 0.05, 1)

    def test_request_replay_does_not_consume_additional_quota(self):
        first, _ = self.one.start(self.request)
        self.one.finish(first["id"], "failed", first)
        repeated, fresh = self.one.start(self.request)
        self.assertFalse(fresh)
        self.assertEqual(first["id"], repeated["id"])
        self.one.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))

    def test_postgres_adapter_uses_bound_parameters_and_transaction_lock(self):
        raw = Mock()
        db = PgConnection(raw)
        db.execute("BEGIN IMMEDIATE")
        self.assertIn("pg_advisory_xact_lock", raw.execute.call_args.args[0])
        db.execute("SELECT * FROM study_runs WHERE id=?", ("user-input",))
        sql, params = raw.execute.call_args.args
        self.assertEqual(sql, "SELECT * FROM cp_demo_study_runs WHERE id=%s")
        self.assertEqual(params, ("user-input",))
        self.assertEqual(PgRow(cost=0.1, uncertain=1, calls=2)[0], 0.1)


if __name__ == "__main__":
    unittest.main()

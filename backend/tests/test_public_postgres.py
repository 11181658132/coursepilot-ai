"""Real PostgreSQL integration; only against an explicit disposable loopback test DB."""

import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from app.services.public_study_store import PgConnection, PublicStudyStore
from app.services.study_agent import PlanRequest
from app.services.study_store import StudyBudgetExceeded

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

    def test_rotating_anonymous_visitor_preserves_ip_quota(self):
        self.one.start(self.request)
        changed = IntegrationStore(DSN, "c" * 64, "1" * 64, self.schema)
        changed.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))
        with self.assertRaises(StudyBudgetExceeded):
            changed.start(self.request.model_copy(update={"request_id": uuid.uuid4()}))


if __name__ == "__main__":
    unittest.main()

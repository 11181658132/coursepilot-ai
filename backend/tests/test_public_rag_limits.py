import hashlib
import hmac
import importlib.util
import io
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.public_rag_limits import PublicRagLimits
from app.services.study_store import StudyStore

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rag_limit_worker",
                                            ROOT / "frontend/api/rag-limit.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class SQLiteRagLimits(PublicRagLimits):
    """Shared limit logic only; real PostgreSQL concurrency is verified in CI."""
    connect = StudyStore.connect

    def __init__(self, path):
        self.path = path
        super().__init__("test-only")
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS public_demo_limits "
                       "(key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, "
                       "PRIMARY KEY (key, day))")


class RagLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "limits.sqlite3"
        self.limits = SQLiteRagLimits(self.path)
        self.now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)

    def quota(self, consume=False, key="a" * 64, now=None):
        return self.limits.quota(key, consume=consume, now=now or self.now)

    def test_status_does_not_consume_or_write_rows(self):
        for _ in range(3):
            self.assertEqual(self.quota()["remaining_today"], 5)
        with self.limits.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM public_demo_limits").fetchone()[0], 0)

    def test_five_requests_survive_store_recreation_and_sixth_is_denied(self):
        for i in range(5):
            self.assertEqual(self.quota(True)["remaining_today"], 4 - i)
        restarted = SQLiteRagLimits(self.path)
        denied = restarted.quota("a" * 64, consume=True, now=self.now)
        self.assertFalse(denied["allowed"])
        self.assertEqual(denied["reason"], "daily")
        self.assertEqual(denied["remaining_month"], 295)
        self.assertEqual(denied["retry_after_seconds"], 43200)

    def test_concurrent_consumption_cannot_exceed_daily_limit(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: self.quota(True), range(10)))
        self.assertEqual(sum(item["allowed"] for item in results), 5)
        self.assertEqual(self.quota()["remaining_month"], 295)

    def test_global_monthly_limit_is_shared_and_atomic_across_ips(self):
        with self.limits.connect() as db:
            db.execute("INSERT INTO public_demo_limits VALUES ('rag:global', '2026-10-01', 299)")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: self.quota(True, key=f"{i:064x}"), range(4)))
        self.assertEqual(sum(item["allowed"] for item in results), 1)
        denied = self.quota(True, key="f" * 64)
        self.assertEqual(denied["reason"], "monthly")
        self.assertEqual(denied["remaining_today"], 5)  # Denial never consumes the IP counter.
        self.assertEqual(denied["retry_at"], "2026-11-01T00:00:00+00:00")

    def test_utc_daily_monthly_and_year_rollovers_are_independent(self):
        for _ in range(5):
            self.quota(True)
        next_day = self.quota(now=datetime(2026, 10, 22, tzinfo=timezone.utc))
        self.assertEqual(next_day["remaining_today"], 5)
        self.assertEqual(next_day["remaining_month"], 295)
        next_month = self.quota(now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual(next_month["remaining_month"], 300)
        with self.limits.connect() as db:
            db.execute("INSERT INTO public_demo_limits VALUES ('rag:global', '2026-12-01', 300)")
        denied = self.quota(now=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(denied["retry_after_seconds"], 1)
        self.assertEqual(denied["retry_at"], "2027-01-01T00:00:00+00:00")

    def test_rag_counters_do_not_change_agent_quotas(self):
        with self.limits.connect() as db:
            db.execute("INSERT INTO public_demo_limits VALUES ('global', '2026-10-21', 20)")
        self.assertTrue(self.quota(True)["allowed"])
        with self.limits.connect() as db:
            self.assertEqual(db.execute("SELECT count FROM public_demo_limits WHERE key='global'"
                                       ).fetchone()[0], 20)

    def test_partial_database_failure_rolls_back_both_counters(self):
        with self.limits.connect() as db:
            db.executescript("CREATE TRIGGER fail_month BEFORE INSERT ON public_demo_limits "
                             "WHEN NEW.key='rag:global' BEGIN "
                             "SELECT RAISE(ABORT, 'test failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            self.quota(True)
        with self.limits.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM public_demo_limits").fetchone()[0], 0)

    def test_rejects_raw_ips_or_unbounded_keys_without_rows(self):
        for key in ("203.0.113.9", "a" * 65, "x" * 64, None):
            with self.assertRaises(ValueError):
                self.quota(True, key=key)


class QuotaWorkerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DEMO_RATE_LIMIT_SALT": "s" * 32,
                                          "DATABASE_URL": "postgresql://test.invalid/demo"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_signature_is_required_domain_separated_and_short_lived(self):
        raw, timestamp = b"{}", str(int(time.time()))
        signature = hmac.new(b"s" * 32, b"rag-limit-v1." + timestamp.encode() + b"." + raw,
                             hashlib.sha256).hexdigest()
        self.assertTrue(worker.valid_signature(raw, timestamp, signature))
        self.assertFalse(worker.valid_signature(raw, "0", signature))
        self.assertFalse(worker.valid_signature(raw + b"x", timestamp, signature))
        foreign = hmac.new(b"s" * 32, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        self.assertFalse(worker.valid_signature(raw, timestamp, foreign))

    def test_unsigned_http_requests_do_not_reach_database(self):
        request = worker.handler.__new__(worker.handler)
        request.headers = {"Content-Length": "2"}
        request.rfile, request.reply = io.BytesIO(b"{}"), Mock()
        with patch.object(worker, "handle") as handle:
            request.do_POST()
            handle.assert_not_called()
        self.assertEqual(request.reply.call_args.args[0], 403)

    def test_worker_accepts_only_quota_actions_and_fails_closed_without_db(self):
        factory = Mock()
        body = {"action": "status", "quota_key": "a" * 64}
        worker.handle(body, factory)
        factory.return_value.quota.assert_called_once_with("a" * 64, consume=False)
        worker.handle({**body, "action": "consume"}, factory)
        self.assertEqual(factory.return_value.quota.call_count, 2)
        for change in ({"action": "refund"}, {"quota_key": "raw-ip"}, {"limit": 500}):
            with self.assertRaises(ValueError):
                worker.handle({**body, **change}, factory)
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with self.assertRaises(RuntimeError):
                worker.handle(body, factory)
        self.assertEqual(factory.return_value.quota.call_count, 2)


if __name__ == "__main__":
    unittest.main()

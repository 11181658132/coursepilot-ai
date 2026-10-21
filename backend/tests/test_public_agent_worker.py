import hashlib
import hmac
import importlib.util
import os
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("public_agent_worker", ROOT / "frontend/api/agent.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class PublicAgentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DEMO_RATE_LIMIT_SALT": "a" * 32,
            "DEMO_AGENT_ENABLED": "true", "DATABASE_URL": "postgresql://test.invalid/demo"})
        self.environment.start()
        self.store = Mock()
        self.factory = Mock(return_value=self.store)
        self.base = Mock()

    def tearDown(self):
        self.environment.stop()

    def envelope(self, action, data):
        return {"tenant": "a" * 64, "quota_key": "b" * 64, "action": action, "data": data}

    def test_worker_signature_is_domain_separated_and_expires(self):
        raw = b"{}"
        timestamp = str(int(time.time()))
        signature = hmac.new(b"a" * 32, b"agent-v1." + timestamp.encode() + b"." + raw,
                             hashlib.sha256).hexdigest()
        self.assertTrue(worker.valid_signature(raw, timestamp, signature))
        self.assertFalse(worker.valid_signature(raw, "0", signature))
        self.assertFalse(worker.valid_signature(raw + b" ", timestamp, signature))
        rag_signature = hmac.new(b"a" * 32, timestamp.encode() + b"." + raw,
                                 hashlib.sha256).hexdigest()
        self.assertFalse(worker.valid_signature(raw, timestamp, rag_signature))

    def test_disabled_or_unconfigured_database_fails_closed(self):
        for variable in ("DATABASE_URL", "DEMO_AGENT_ENABLED"):
            with patch.dict(os.environ, {variable: ""}):
                with self.assertRaises(RuntimeError):
                    worker.handle(self.envelope("list", {"course": "DEMO101"}), self.factory)
        self.factory.assert_not_called()

    def test_private_course_and_large_plan_are_rejected_before_store_or_model(self):
        for course, days in (("90049", 1), ("DEMO101", 7)):
            body = self.envelope("draft", {"request_id": str(uuid.uuid4()), "course": course,
                                           "goal": "Review", "days": days, "daily_minutes": 60})
            with self.assertRaises(ValueError):
                worker.handle(body, self.factory, self.base)
        self.factory.assert_not_called()
        self.base.assert_not_called()

    def test_confirmation_and_progress_do_not_load_model_or_index(self):
        body = self.envelope("confirm", {"plan_id": "plan", "confirmed": True, "version": 1})
        worker.handle(body, self.factory, self.base)
        self.store.confirm.assert_called_once_with("plan", 1)
        body = self.envelope("task", {"plan_id": "plan", "task_id": "task", "done": True,
                                       "version": 2})
        worker.handle(body, self.factory, self.base)
        self.store.set_task.assert_called_once_with("plan", "task", True, 2)
        self.base.assert_not_called()

    def test_false_confirmation_and_injected_parameters_are_rejected(self):
        body = self.envelope("confirm", {"plan_id": "plan", "confirmed": False, "version": 1})
        with self.assertRaises(ValueError):
            worker.handle(body, self.factory, self.base)
        body = self.envelope("list", {"course": "DEMO101"})
        body["database"] = "attacker-host"
        with self.assertRaises(ValueError):
            worker.handle(body, self.factory, self.base)
        self.store.confirm.assert_not_called()

    def test_feedback_confirmation_does_not_load_model_or_index(self):
        body = self.envelope("confirm_feedback", {"plan_id": "plan", "feedback_id": "feedback",
                                                   "confirmed": True, "version": 3})
        worker.handle(body, self.factory, self.base)
        self.store.confirm_feedback.assert_called_once_with("plan", "feedback", 3)
        self.base.assert_not_called()

    def test_feedback_rejects_large_answer_or_foreign_course_before_model(self):
        body = self.envelope("feedback", {"plan_id": "plan", "task_id": "task",
            "request_id": str(uuid.uuid4()), "version": 2, "answer": "x" * 401})
        with self.assertRaises(ValueError):
            worker.handle(body, self.factory, self.base)
        body["data"]["answer"] = "Test answer"
        self.store.get.return_value = {"course": "PRIVATE"}
        with self.assertRaises(KeyError):
            worker.handle(body, self.factory, self.base)
        self.base.assert_not_called()


if __name__ == "__main__":
    unittest.main()

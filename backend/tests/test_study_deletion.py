import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.config import Settings, get_settings
from app.main import app, get_study_store
from app.services.study_agent import PlanRequest
from app.services.study_feedback import FeedbackRequest
from app.services.study_store import StudyConflict, StudyStore
from fastapi.testclient import TestClient


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StudyStore(Path(self.temp.name) / "plans.sqlite3")
        self.request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", goal="GOAL_SECRET")
        state, _ = self.store.start(self.request)
        self.identifier = state["id"]
        self.task = {"id": uuid.uuid4().hex, "day": 1, "done": False}

    def feedback(self):
        self.store.finish(
            self.identifier,
            "draft",
            {"request": self.request.model_dump(mode="json"), "tasks": [self.task], "trace": []},
        )
        parent = self.store.confirm(self.identifier, 1)
        child, _ = self.store.begin_feedback(
            self.identifier,
            self.task["id"],
            FeedbackRequest(
                request_id=uuid.uuid4(), version=parent["version"], answer="ANSWER_SECRET"
            ),
        )
        self.store.reserve(child["id"], 0.02, 0.05, 0.5)
        return self.store.get(self.identifier), self.store.get(child["id"])

    def test_erases_parent_and_feedback_but_preserves_cost_and_deduplication(self):
        parent, child = self.feedback()
        self.store.finish(child["id"], "failed", self.store.payload(child))
        self.assertEqual(self.store.delete(self.identifier, parent["version"]), {"deleted": True})
        for identifier in (self.identifier, child["id"]):
            with self.assertRaises(KeyError):
                self.store.get(identifier)
        self.assertEqual(self.store.list("DEMO101"), [])
        self.assertEqual(self.store.list("_deleted"), [])
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM study_runs").fetchall()
            for row in rows:
                self.assertEqual(row["status"], "deleted")
                self.assertEqual(row["fingerprint"], "deleted")
                self.assertEqual(row["course"], "_deleted")
                self.assertNotIn("SECRET", row["payload"])
                self.assertEqual(set(json.loads(row["payload"])), {"kind", "deleted_at"})
            self.assertEqual(
                db.execute("SELECT SUM(amount) FROM study_charges").fetchone()[0], 0.02
            )
        with self.assertRaises(StudyConflict):
            self.store.start(self.request)
        self.assertEqual(self.store.delete(self.identifier, 0), {"deleted": True})

    def test_live_parent_or_feedback_prevents_deletion_but_expired_run_can_be_erased(self):
        with self.assertRaises(StudyConflict):
            self.store.delete(self.identifier, 0)
        parent, child = self.feedback()
        with self.assertRaises(StudyConflict):
            self.store.delete(self.identifier, parent["version"])
        with patch(
            "app.services.study_store.time.time", return_value=child["lease_expires_at"] + 1
        ):
            self.store.delete(self.identifier, parent["version"])
        for operation in (
            lambda: self.store.finish(child["id"], "draft", child),
            lambda: self.store.checkpoint(child["id"], child),
            lambda: self.store.reserve(child["id"], 0.02, 0.05, 0.5),
        ):
            with self.assertRaises(StudyConflict):
                operation()

    def test_stale_version_and_direct_feedback_deletion_cannot_remove_content(self):
        parent, child = self.feedback()
        self.store.finish(child["id"], "failed", self.store.payload(child))
        with self.assertRaises(StudyConflict):
            self.store.delete(self.identifier, parent["version"] - 1)
        with self.assertRaises(KeyError):
            self.store.delete(child["id"], 1)
        self.assertIn("GOAL_SECRET", json.dumps(self.store.get(self.identifier)))

    def test_concurrent_deletion_is_atomic_and_idempotent(self):
        self.store.finish(
            self.identifier, "failed", {"request": self.request.model_dump(mode="json")}
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: self.store.delete(self.identifier, 1), range(2)))
        self.assertEqual(result, [{"deleted": True}] * 2)

    def test_api_requires_explicit_confirmation_and_returns_404_after_delete(self):
        self.store.finish(
            self.identifier, "failed", {"request": self.request.model_dump(mode="json")}
        )
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
        app.dependency_overrides[get_study_store] = lambda: self.store
        self.addCleanup(app.dependency_overrides.clear)
        client = TestClient(app)
        route = f"/api/study-plans/{self.identifier}"
        self.assertEqual(
            client.request("DELETE", route, json={"confirmed": False, "version": 1}).status_code,
            422,
        )
        self.assertEqual(
            client.request("DELETE", route, json={"confirmed": True, "version": 1}).status_code, 200
        )
        self.assertEqual(client.get(route).status_code, 404)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings, get_settings
from app.main import app, get_knowledge_base
from fastapi.testclient import TestClient


class FakeKnowledgeBase:
    def __init__(self) -> None:
        self.deleted = []

    def delete_document(self, relative_path: str) -> bool:
        self.deleted.append(relative_path)
        return False


class DocumentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.settings = Settings(
            _env_file=None,
            knowledge_base_dir=root / "knowledge",
            index_state_path=root / "state.json",
            telemetry_path=root / "events.jsonl",
            conversation_db_path=root / "conversations.db",
            rag_db_path=root / "rag.sqlite3",
        )
        self.knowledge_base = FakeKnowledgeBase()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_knowledge_base] = lambda: self.knowledge_base
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.temporary_directory.cleanup()

    def test_upload_inventory_and_explicit_delete(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            params={
                "course": "90016",
                "material_type": "lecture",
                "filename": "Week 1.pdf",
            },
            content=b"%PDF-test document",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["document"]["status"], "pending")

        inventory = self.client.get("/api/documents")
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(len(inventory.json()), 1)

        deleted = self.client.request(
            "DELETE",
            "/api/documents",
            json={"path": "90016/lecture/Week 1.pdf"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["local_deleted"])
        self.assertEqual(self.knowledge_base.deleted, ["90016/lecture/Week 1.pdf"])

    def test_rejects_spoofed_pdf_content(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            params={
                "course": "90016",
                "material_type": "lecture",
                "filename": "not-really.pdf",
            },
            content=b"plain text",
        )
        self.assertEqual(response.status_code, 422)

    def upload(self, **params):
        return self.client.post("/api/documents/upload", params={
            "course": "DEMO101", "material_type": "lecture", "filename": "fictional.md",
            **params}, content=b"Fictional source for indexing tests.")

    def test_upload_is_file_only_by_default_and_opt_in_returns_durable_job(self):
        self.assertIsNone(self.upload().json()["index_job"])
        with patch("app.services.hybrid_kb.OpenAI") as sdk:
            first = self.upload(index_after_upload=True, engine="hybrid")
            second = self.upload(index_after_upload=True, engine="hybrid")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["index_job"]["id"], second.json()["index_job"]["id"])
        self.assertNotIn("scope", first.json()["index_job"])
        sdk.assert_not_called()

    def test_queue_failure_reports_saved_file_and_identical_retry_recovers(self):
        with patch("app.main.JobStore", side_effect=OSError("PRIVATE details")):
            failed = self.upload(index_after_upload=True, engine="hybrid")
        self.assertEqual(failed.status_code, 503)
        self.assertIn("文件已保存", failed.json()["detail"])
        self.assertNotIn("PRIVATE", failed.text)
        recovered = self.upload(index_after_upload=True, engine="hybrid")
        self.assertEqual(recovered.status_code, 200)

    def test_hosted_auto_indexing_is_rejected_before_saving(self):
        response = self.upload(index_after_upload=True, engine="file_search")
        self.assertEqual(response.status_code, 422)
        path = self.settings.knowledge_base_dir / "DEMO101/lecture/fictional.md"
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

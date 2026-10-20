import hashlib
import hmac
import importlib.util
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("public_worker", ROOT / "frontend/api/rag.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class PublicWorkerTests(unittest.TestCase):
    def test_signature_is_required_and_expires(self):
        body = b'{"course":"DEMO101"}'
        timestamp = str(int(time.time()))
        with patch.dict(os.environ, {"DEMO_RATE_LIMIT_SALT": "test-only"}):
            signature = hmac.new(b"test-only", timestamp.encode() + b"." + body,
                                 hashlib.sha256).hexdigest()
            self.assertTrue(worker.valid_signature(body, timestamp, signature))
            self.assertFalse(worker.valid_signature(body + b" ", timestamp, signature))
            self.assertFalse(worker.valid_signature(body, "0", signature))
            self.assertFalse(worker.valid_signature(body, "invalid", signature))
        with patch.dict(os.environ, {"DEMO_RATE_LIMIT_SALT": ""}):
            self.assertFalse(worker.valid_signature(body, timestamp, signature))

    def test_private_course_is_rejected_before_loading_index(self):
        with patch.object(worker, "demo_base") as base:
            with self.assertRaises(ValueError):
                worker.answer_request({"course": "90049", "question": "test",
                                       "material_type": "all"})
            base.assert_not_called()

    def test_public_index_contains_only_versioned_fictional_sources(self):
        index = json.loads((ROOT / "frontend/demo-index.json").read_text())
        self.assertEqual(len(index["documents"]), 5)
        for document in index["documents"]:
            self.assertTrue(document["path"].startswith("DEMO101/"))
            filename = Path(document["path"]).name
            source = ROOT / "frontend/demo-knowledge" / filename
            self.assertIn(filename, index["allowed_files"])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), document["sha256"])
        for chunk in index["chunks"]:
            self.assertEqual(chunk["course"], "DEMO101")
            self.assertEqual(len(chunk["embedding"]), 1536)


if __name__ == "__main__":
    unittest.main()

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.services.documents import (
    DocumentValidationError,
    build_upload_path,
    list_documents,
    resolve_document_path,
    save_upload,
)


async def chunks(*values: bytes):
    for value in values:
        yield value


class DocumentServiceTests(unittest.TestCase):
    def test_builds_safe_unicode_course_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = build_upload_path(root, "90002新", "lecture", "Week 1.pdf")
            self.assertEqual(target, root.resolve() / "90002新" / "lecture" / "Week 1.pdf")

    def test_rejects_path_traversal_and_unsupported_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(DocumentValidationError):
                resolve_document_path(root, "../../secret.txt")
            with self.assertRaises(DocumentValidationError):
                build_upload_path(root, "90016", "lecture", "../secret.pdf")
            with self.assertRaises(DocumentValidationError):
                build_upload_path(root, "90016", "lecture", "image.exe")

    def test_saves_upload_atomically_and_rejects_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, size = asyncio.run(
                save_upload(
                    root,
                    "90016",
                    "tutorial",
                    "Tute 1.pdf",
                    chunks(b"%PDF-", b"abcdef"),
                    20,
                )
            )
            self.assertEqual(size, 11)
            self.assertEqual(target.read_bytes(), b"%PDF-abcdef")
            with self.assertRaises(DocumentValidationError):
                asyncio.run(
                    save_upload(
                        root,
                        "90016",
                        "tutorial",
                        "Tute 2.pdf",
                        chunks(b"too large"),
                        3,
                    )
                )
            self.assertFalse((root / "90016" / "tutorial" / "Tute 2.pdf").exists())

    def test_lists_indexed_pending_and_missing_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "90049" / "lecture" / "week1.pdf"
            current.parent.mkdir(parents=True)
            current.write_bytes(b"current")
            state = {
                "files": {
                    "90049/lecture/week1.pdf": {
                        "sha256": "outdated",
                        "course": "90049",
                        "material_type": "lecture",
                    },
                    "90049/tutorial/tute1.pdf": {
                        "sha256": "old",
                        "course": "90049",
                        "material_type": "tutorial",
                    },
                }
            }
            records = list_documents(root, state)
            self.assertEqual([item["status"] for item in records], ["pending", "missing"])


if __name__ == "__main__":
    unittest.main()

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
    def test_cancelled_upload_removes_temporary_file_without_publishing(self):
        async def cancelled():
            yield b"partial text"
            raise asyncio.CancelledError()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(save_upload(root, "A", "lecture", "cancelled.md", cancelled(), 100))
            self.assertFalse((root / "A/lecture/cancelled.md").exists())
            self.assertEqual(list(root.rglob("*.upload")), [])

    def test_opt_in_identical_upload_reuses_file_but_never_overwrites_different_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = asyncio.run(save_upload(root, "A", "lecture", "a.md",
                                            chunks(b"same content"), 100))
            again = asyncio.run(save_upload(root, "A", "lecture", "a.md",
                                            chunks(b"same content"), 100, allow_identical=True))
            self.assertEqual(first, again)
            with self.assertRaises(FileExistsError):
                asyncio.run(save_upload(root, "A", "lecture", "a.md",
                                        chunks(b"different"), 100, allow_identical=True))
            self.assertEqual(first[0].read_bytes(), b"same content")
            self.assertEqual(list(root.rglob("*.upload")), [])

    def test_concurrent_same_name_upload_never_overwrites(self):
        async def race(root):
            both_started = asyncio.Event()
            started = 0

            async def stream(content):
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
                yield content

            return await asyncio.gather(*[
                save_upload(root, "A", "lecture", "same.md", stream(content), 100)
                for content in [b"first upload", b"second upload"]
            ], return_exceptions=True)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = asyncio.run(race(root))
            self.assertEqual(sum(isinstance(item, FileExistsError) for item in results), 1)
            self.assertIn((root / "A/lecture/same.md").read_bytes(),
                          [b"first upload", b"second upload"])
            self.assertEqual(len(list(root.rglob("*.upload"))), 0)

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

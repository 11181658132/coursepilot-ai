import tempfile
import unittest
from pathlib import Path

from app.services.library import classify_material, discover_documents, summarize_courses


class LibraryTests(unittest.TestCase):
    def test_classifies_tute_filename_as_tutorial(self) -> None:
        self.assertEqual(classify_material(Path("90016/files/Week_2_Tute.pdf")), "tutorial")

    def test_discovers_supported_files_and_summarizes_courses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            lecture = root / "90016" / "lecture" / "week1.pdf"
            tutorial = root / "90016" / "tutorial" / "tute1.pptx"
            ignored = root / "90016" / "lecture" / "notes.jpg"
            lecture.parent.mkdir(parents=True)
            tutorial.parent.mkdir(parents=True)
            lecture.write_bytes(b"lecture")
            tutorial.write_bytes(b"tutorial")
            ignored.write_bytes(b"image")

            documents = discover_documents(root)
            self.assertEqual(len(documents), 2)
            summary = summarize_courses(documents, ["90016/lecture/week1.pdf"])
            self.assertEqual(summary[0]["lecture_count"], 1)
            self.assertEqual(summary[0]["tutorial_count"], 1)
            self.assertEqual(summary[0]["indexed_count"], 1)


if __name__ == "__main__":
    unittest.main()

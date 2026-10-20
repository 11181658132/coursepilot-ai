import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".txt", ".md", ".docx"}
TUTORIAL_MARKERS = ("tutorial", "tute", "tut_")
LECTURE_MARKERS = ("lecture", "lect", "week", "module")


@dataclass(frozen=True)
class Document:
    absolute_path: Path
    relative_path: str
    course: str
    material_type: str
    sha256: str

    @property
    def bucket(self) -> str:
        return f"{self.course}:{self.material_type}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_material(relative_path: Path) -> str:
    lowered_parts = [part.lower() for part in relative_path.parts]
    joined = "/".join(lowered_parts)
    if any(marker in joined for marker in TUTORIAL_MARKERS):
        return "tutorial"
    if any(marker in joined for marker in LECTURE_MARKERS):
        return "lecture"
    return "other"


def discover_documents(root: Path) -> List[Document]:
    if not root.exists() or not root.is_dir():
        return []

    documents: List[Document] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        relative = path.relative_to(root)
        if len(relative.parts) < 2 or relative.name.startswith("~$"):
            continue
        documents.append(
            Document(
                absolute_path=path,
                relative_path=relative.as_posix(),
                course=relative.parts[0],
                material_type=classify_material(relative),
                sha256=sha256_file(path),
            )
        )
    return documents


def summarize_courses(
    documents: Iterable[Document], indexed_paths: Iterable[str] = ()
) -> List[Dict[str, object]]:
    indexed = set(indexed_paths)
    courses: Dict[str, Dict[str, object]] = {}
    for document in documents:
        summary = courses.setdefault(
            document.course,
            {
                "course": document.course,
                "lecture_count": 0,
                "tutorial_count": 0,
                "other_count": 0,
                "indexed_count": 0,
            },
        )
        key = f"{document.material_type}_count"
        summary[key] = int(summary[key]) + 1
        if document.relative_path in indexed:
            summary["indexed_count"] = int(summary["indexed_count"]) + 1
    return [courses[name] for name in sorted(courses)]

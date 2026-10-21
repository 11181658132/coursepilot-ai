import os
import tempfile
import unicodedata
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.services.library import SUPPORTED_EXTENSIONS, discover_documents

MATERIAL_TYPES = {"lecture", "tutorial", "other"}


class DocumentValidationError(ValueError):
    pass


def _safe_segment(value: str, label: str, max_length: int = 80) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or len(normalized) > max_length
        or any(character in normalized for character in ("/", "\\", "\0"))
    ):
        raise DocumentValidationError(f"Invalid {label}")
    return normalized


def resolve_document_path(root: Path, relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized or "\0" in normalized:
        raise DocumentValidationError("Invalid document path")
    root_resolved = root.resolve()
    candidate = (root_resolved / normalized).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise DocumentValidationError("Document path escapes the knowledge base") from exc
    return candidate


def build_upload_path(root: Path, course: str, material_type: str, filename: str) -> Path:
    safe_course = _safe_segment(course, "course")
    if material_type not in MATERIAL_TYPES:
        raise DocumentValidationError("Invalid material type")
    safe_filename = _safe_segment(filename, "filename", max_length=200)
    if Path(safe_filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentValidationError(f"Unsupported file type. Allowed: {allowed}")
    relative_path = f"{safe_course}/{material_type}/{safe_filename}"
    return resolve_document_path(root, relative_path)


def _validate_file_signature(path: Path, suffix: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(4096)
    if suffix == ".pdf" and not header.startswith(b"%PDF-"):
        raise DocumentValidationError("File content does not match the .pdf extension")
    if suffix in {".pptx", ".docx"} and not header.startswith(b"PK"):
        raise DocumentValidationError(f"File content does not match the {suffix} extension")
    if suffix in {".txt", ".md"} and b"\0" in header:
        raise DocumentValidationError("Text files cannot contain binary null bytes")


async def save_upload(
    root: Path,
    course: str,
    material_type: str,
    filename: str,
    chunks: AsyncIterable[bytes],
    max_bytes: int,
) -> Tuple[Path, int]:
    if max_bytes <= 0:
        raise DocumentValidationError("Upload limit must be positive")
    target = build_upload_path(root, course, material_type, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"A file named {target.name} already exists")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".upload", dir=str(target.parent)
    )
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise DocumentValidationError(
                        f"File exceeds the {max_bytes // (1024 * 1024)} MB limit"
                    )
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if total == 0:
            raise DocumentValidationError("Uploaded file is empty")
        _validate_file_signature(Path(temporary_name), target.suffix.lower())
        # Atomic create-if-absent: two concurrent uploads must never overwrite.
        os.link(temporary_name, target)
        os.unlink(temporary_name)
        return target, total
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def list_documents(root: Path, state: Dict[str, Any]) -> List[Dict[str, object]]:
    records: Dict[str, Dict[str, object]] = {}
    indexed_files = state.get("files", {})
    for document in discover_documents(root):
        indexed = indexed_files.get(document.relative_path)
        status = (
            "indexed"
            if indexed and indexed.get("sha256") == document.sha256
            else "pending"
        )
        records[document.relative_path] = {
            "path": document.relative_path,
            "filename": document.absolute_path.name,
            "course": document.course,
            "material_type": document.material_type,
            "size_bytes": document.absolute_path.stat().st_size,
            "status": status,
        }
    for relative_path, indexed in indexed_files.items():
        if relative_path in records:
            continue
        records[relative_path] = {
            "path": relative_path,
            "filename": Path(relative_path).name,
            "course": indexed.get("course") or Path(relative_path).parts[0],
            "material_type": indexed.get("material_type", "other"),
            "size_bytes": 0,
            "status": "missing",
        }
    return [records[path] for path in sorted(records)]

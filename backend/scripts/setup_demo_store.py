import json
import time
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from openai import OpenAI

DEMO_DIRECTORY = PROJECT_ROOT / "frontend" / "demo-knowledge"
STATE_PATH = PROJECT_ROOT / "backend" / "data" / "demo_store.json"


def material_type(path: Path) -> str:
    return "tutorial" if path.name.startswith("tutorial-") else "lecture"


def wait_until_ready(client: OpenAI, store_id: str, file_id: str) -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        item = client.vector_stores.files.retrieve(
            vector_store_id=store_id,
            file_id=file_id,
        )
        if item.status == "completed":
            return
        if item.status == "failed":
            raise RuntimeError(f"Indexing failed for {file_id}: {item.last_error}")
        time.sleep(1.5)
    raise TimeoutError(f"Timed out indexing {file_id}")


def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not configured")

    files = sorted(DEMO_DIRECTORY.glob("*.md"))
    if not files:
        raise SystemExit(f"No demo files found in {DEMO_DIRECTORY}")

    client = OpenAI(api_key=settings.openai_api_key)
    store = client.vector_stores.create(
        name="CoursePilot Public Demo",
        description="Fictional, public DEMO101 material for the CoursePilot portfolio demo.",
    )
    uploaded_ids: list[str] = []
    try:
        for path in files:
            with path.open("rb") as handle:
                uploaded = client.files.create(file=handle, purpose="assistants")
            uploaded_ids.append(uploaded.id)
            client.vector_stores.files.create(
                vector_store_id=store.id,
                file_id=uploaded.id,
                attributes={"course": "DEMO101", "material_type": material_type(path)},
            )
            wait_until_ready(client, store.id, uploaded.id)

        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "vector_store_id": store.id,
                    "file_ids": uploaded_ids,
                    "files": [path.name for path in files],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(store.id)
    except Exception:
        for file_id in uploaded_ids:
            try:
                client.files.delete(file_id)
            except Exception:
                pass
        client.vector_stores.delete(store.id)
        raise


if __name__ == "__main__":
    main()

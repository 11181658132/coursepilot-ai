"""Small live smoke test using only the five public fictional demo documents.

Run from backend with PYTHONPATH=. ../.venv/bin/python scripts/smoke_hybrid.py.
Creates no remote vector store and removes the temporary local index on exit.
"""

import json
import shutil
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.services.hybrid_kb import HybridKnowledgeBase


def main():
    with tempfile.TemporaryDirectory(prefix="coursepilot-smoke-") as temporary:
        root = Path(temporary)
        knowledge = root / "knowledge"
        for source in (PROJECT_ROOT / "frontend/demo-knowledge").glob("*.md"):
            kind = "tutorial" if source.name.startswith("tutorial") else "lecture"
            target = knowledge / "DEMO101" / kind / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        config = get_settings().model_copy(update={
            "knowledge_base_dir": knowledge,
            "rag_db_path": root / "index.sqlite3",
            "telemetry_path": root / "events.jsonl",
            "rag_index_max_embedding_tokens": 20_000,
            "max_search_results": 4,
        })
        base = HybridKnowledgeBase(config)
        report = base.sync(knowledge)
        if report.failed or report.uploaded != 5:
            raise RuntimeError("Fictional sample indexing failed")
        answer = base.chat(
            "DEMO101", "lecture", "What is RAG and how does it differ from fine-tuning?"
        )
        passed = bool(answer.citations and all(c.quote and c.chunk_id for c in answer.citations))
        print(json.dumps({
            "passed": passed, "indexed_files": report.uploaded,
            "chunks": report.chunk_count, "citations": len(answer.citations),
            "latency_ms": answer.latency_ms,
            "estimated_total_cost_usd": round(
                report.estimated_cost_usd + answer.estimated_cost_usd, 8),
        }, indent=2))
        if not passed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

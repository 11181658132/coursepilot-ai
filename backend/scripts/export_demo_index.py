"""Export only fictional public documents with canonical chunks and embeddings."""

import json
import shutil
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.services.hybrid_kb import HybridKnowledgeBase


def main():
    frontend = PROJECT_ROOT / "frontend"
    sources = sorted((frontend / "demo-knowledge").glob("*.md"))
    if len(sources) != 5:
        raise ValueError("Expected exactly five fictional demo documents")
    with tempfile.TemporaryDirectory(prefix="coursepilot-export-") as temporary:
        root = Path(temporary)
        knowledge = root / "knowledge"
        for source in sources:
            material = "tutorial" if source.name.startswith("tutorial") else "lecture"
            target = knowledge / "DEMO101" / material / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        config = get_settings().model_copy(update={
            "knowledge_base_dir": knowledge, "rag_db_path": root / "rag.sqlite3",
            "rag_index_max_embedding_tokens": 20_000,
        })
        base = HybridKnowledgeBase(config)
        report = base.sync(knowledge)
        if report.failed or report.uploaded != 5:
            raise RuntimeError("Demo index export failed")
        rows = base.store.rows(base.signature, "DEMO101", "all")
        documents = [{key: value for key, value in document.items() if key != "signature"}
                     for document in base.manifest().values()]
        result = {"version": 1, "embedding_model": config.rag_embedding_model,
                  "chunk_chars": config.rag_chunk_chars, "chunk_overlap": config.rag_chunk_overlap,
                  "allowed_files": [source.name for source in sources], "documents": documents,
                  "chunks": [{key: value for key, value in row.items() if key != "sha256"}
                             for row in rows]}
        (frontend / "demo-index.json").write_text(json.dumps(result), encoding="utf-8")
        print(json.dumps({"documents": len(documents), "chunks": len(rows),
                          "estimated_cost_usd": report.estimated_cost_usd}))


if __name__ == "__main__":
    main()

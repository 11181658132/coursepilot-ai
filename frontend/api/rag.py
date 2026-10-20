"""Private signed worker reusing the canonical Python hybrid RAG implementation.

Only fictional, versioned demo passages are available in this deployment.
Public requests go through /api/demo/chat, which applies limits before signing.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_LOCK = Lock()


def valid_signature(body: bytes, timestamp: str, signature: str) -> bool:
    salt = os.environ.get("DEMO_RATE_LIMIT_SALT", "")
    if not salt:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60:
            return False
    except (TypeError, ValueError):
        return False
    expected = hmac.new(salt.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@lru_cache(maxsize=1)
def demo_base():
    sys.path.insert(0, str(ROOT / "_rag_backend"))
    from app.config import Settings
    from app.services.hybrid_kb import HybridKnowledgeBase

    index = json.loads((ROOT / "demo-index.json").read_text())
    directory = Path(tempfile.mkdtemp(prefix="coursepilot-public-"))
    knowledge = directory / "knowledge"
    # Rehydrate only allowlisted fictional source files; no local course paths exist here.
    for document in index["documents"]:
        source_path = Path(document["path"])
        if (source_path.parts[:1] != ("DEMO101",) or ".." in source_path.parts
                or source_path.name not in index["allowed_files"]):
            raise ValueError("Invalid public index source")
        content = (ROOT / "demo-knowledge" / source_path.name).read_bytes()
        if hashlib.sha256(content).hexdigest() != document["sha256"]:
            raise ValueError("Public index is stale")
        target = knowledge / source_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    config = Settings(
        _env_file=None, knowledge_base_dir=knowledge,
        rag_db_path=directory / "rag.sqlite3", telemetry_path=directory / "events.jsonl",
        rag_embedding_model=index["embedding_model"], rag_chunk_chars=index["chunk_chars"],
        rag_chunk_overlap=index["chunk_overlap"], max_search_results=4,
        openai_max_retries=0, openai_timeout_seconds=20,
    )
    base = HybridKnowledgeBase(config)
    for document in index["documents"]:
        chunks = [row for row in index["chunks"] if row["path"] == document["path"]]
        vectors = [row["embedding"] for row in chunks]
        payloads = [{key: value for key, value in row.items() if key != "embedding"}
                    for row in chunks]
        base.store.replace(SimpleNamespace(relative_path=document["path"],
                                           sha256=document["sha256"], course="DEMO101",
                                           material_type=document["material_type"]),
                           base.signature, payloads, vectors)
    return base


def answer_request(body: dict) -> dict:
    question = body.get("question")
    material = body.get("material_type")
    if (body.get("course") != "DEMO101" or material not in {"all", "lecture", "tutorial"}
            or not isinstance(question, str) or not 1 <= len(question.strip()) <= 600):
        raise ValueError("Invalid demo request")
    history = body.get("history", [])
    if not isinstance(history, list):
        raise ValueError("Invalid history")
    clean = [{"role": item["role"], "content": item["content"][:600]} for item in history[-6:]
             if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
             and isinstance(item.get("content"), str)]
    # A warm worker can serve concurrent requests; usage counters must not overlap.
    with _LOCK:
        result = demo_base().chat("DEMO101", material, question.strip(), history=clean)
    return {**result.model_dump(), "engine": "hybrid", "model": demo_base().config.openai_model}


class handler(BaseHTTPRequestHandler):
    def reply(self, status: int, payload: dict, retry_after=None):
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        if retry_after:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 12_000:
                return self.reply(400, {"detail": "Invalid request size"})
            body = self.rfile.read(length)
            if not valid_signature(body, self.headers.get("X-Rag-Timestamp", ""),
                                   self.headers.get("X-Rag-Signature", "")):
                return self.reply(403, {"detail": "Forbidden"})
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Invalid body")
            return self.reply(200, answer_request(payload))
        except (ValueError, TypeError, KeyError):
            return self.reply(400, {"detail": "公开 Demo 请求无效。"})
        except Exception as exc:
            # Never return provider bodies, keys, environment, questions, or passages.
            response = getattr(exc, "response", None)
            status = 429 if getattr(exc, "status_code", None) == 429 else 502
            retry_after = response.headers.get("retry-after") if response is not None else None
            return self.reply(status, {"detail": "AI 服务暂时不可用，请稍后再试。"}, retry_after)

    def log_message(self, format, *args):
        pass

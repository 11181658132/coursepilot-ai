"""Explicit loopback-only fictional ingestion acceptance server. ZERO provider calls.

Not a production mode or a quality benchmark. Never loads .env API credentials.
Start: PYTHONPATH=backend .venv/bin/python -m app.ingestion_demo --directory
evals/reports/<fresh-folder>. Control: POST /acceptance/worker?fail=true|false.
"""

import argparse
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from fastapi.middleware.cors import CORSMiddleware

from app.config import PROJECT_ROOT, Settings, get_settings
from app.main import app, get_hybrid_base, get_knowledge_base, get_study_store
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.study_agent import PlanRequest, StudyAgent
from app.worker import run_once


class FictionalClient:
    def __init__(self):
        self.fail = False
        self.calls = 0
        self.embeddings = SimpleNamespace(create=self.embed)
        self.responses = SimpleNamespace(create=self.respond)

    def embed(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Synthetic indexing failure")
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=0), data=[
            SimpleNamespace(index=i, embedding=[1.0, 0.0]) for i, _ in enumerate(kwargs["input"])])

    def respond(self, **kwargs):
        self.calls += 1
        first = json.loads(kwargs["input"])["passages"][0]
        quote = first["text"][:120]
        return SimpleNamespace(status="completed", output=[], usage=SimpleNamespace(
            input_tokens=0, output_tokens=0), output_text=json.dumps({
                "answer": "SIMULATED acceptance answer: " + quote + " [S1]",
                "citations": [{"source_id": first["source_id"], "quote": quote}]}))


def configure(directory, resume=False):
    root = directory.resolve()
    reports = (PROJECT_ROOT / "evals/reports").resolve()
    if root == reports or not root.is_relative_to(reports):
        raise ValueError("Acceptance directory must stay inside ignored evals/reports")
    root.mkdir(parents=True, exist_ok=resume)
    config = Settings(_env_file=None, openai_api_key="", openai_model="gpt-6-luna",
        knowledge_base_dir=root / "knowledge", rag_db_path=root / "rag.sqlite3",
        conversation_db_path=root / "chat.db", index_state_path=root / "state.json",
        telemetry_path=root / "events.jsonl", rag_engine="hybrid", rag_rerank=False,
        frontend_origin="http://localhost:3014")
    config.knowledge_base_dir.mkdir(exist_ok=resume)
    client = FictionalClient()
    def factory(settings=config):
        return HybridKnowledgeBase(settings, client)
    app.dependency_overrides[get_settings] = lambda: config
    app.dependency_overrides[get_hybrid_base] = lambda: factory()
    # The production route also resolves the hosted dependency before branching.
    # Do not initialize a credentialed hosted client even in a hybrid-only smoke.
    app.dependency_overrides[get_knowledge_base] = lambda: SimpleNamespace()

    @app.post("/acceptance/worker")
    def worker(fail: bool = False):
        client.fail = fail
        worked = run_once(config, factory)
        return {"worked": worked, "simulated_calls": client.calls, "external_api_calls": 0}

    @app.post("/acceptance/agent-draft")
    def draft():
        """Exercise the real tool loop with fixed scripted outputs, never a real SDK."""
        turns = iter(("list_materials", "search_knowledge", "propose_study_plan"))

        def respond(**kwargs):
            tool = next(turns)
            arguments = {} if tool == "list_materials" else {"query": "RAG"}
            if tool == "propose_study_plan":
                evidence = json.loads(kwargs["input"][-1]["output"])["passages"][0]
                arguments = {"title": "[SIMULATED] Evidence review", "tasks": [{
                    "day": day, "title": "Review evidence", "study_goal": "Read source evidence",
                    "quiz_question": "What does the source say?",
                    "quiz_answer": "Check source evidence, not just keyword overlap.",
                    "citation": {"source_id": evidence["source_id"],
                                 "quote": evidence["text"][:120]}} for day in (1, 2, 3)]}
            return SimpleNamespace(status="completed", usage=SimpleNamespace(
                input_tokens=0, output_tokens=0), output=[SimpleNamespace(
                    type="function_call", name=tool, call_id=uuid.uuid4().hex,
                    arguments=json.dumps(arguments))])

        sdk = SimpleNamespace(responses=SimpleNamespace(create=respond))
        request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", days=3,
                              goal="[SIMULATED] Review fictional evidence", daily_minutes=30)
        return StudyAgent(config, get_study_store(config), sdk, factory()).run(request)

    # The production app's middleware was built before dependency overrides.
    return CORSMiddleware(app, allow_origins=["http://localhost:3014", "http://127.0.0.1:3014"],
                          allow_methods=["*"], allow_headers=["*"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8014)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    uvicorn.run(configure(args.directory, args.resume), host="127.0.0.1", port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()

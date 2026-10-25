"""Opt-in passage RAG / learning-task campaign sharing ONE persisted USD budget."""

import argparse
import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from unittest.mock import patch

from openai import OpenAI

from app.challenge_data import (
    DATASET,
    VARIANTS,
    chunk_id,
    load_dataset,
    materialize,
    passage_metrics,
)
from app.config import PROJECT_ROOT, Settings, get_settings
from app.eval_budget import BudgetExceeded, BudgetLedger
from app.services.agent_checks import check_feedback, check_plan
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.retrieval import rank_candidates
from app.services.state import save_state
from app.services.study_agent import PlanRequest, StudyAgent
from app.services.study_feedback import FeedbackAgent, FeedbackRequest
from app.services.study_store import StudyConflict, StudyStore

TASKS = PROJECT_ROOT / "evals/learning_tasks_v1.json"
ALLOWED_TOOLS = {"list_materials", "search_knowledge", "propose_study_plan", "propose_feedback"}


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_tasks():
    data = json.loads(TASKS.read_text())
    cases = data["cases"]
    if (data["version"] != 1 or data["scope"] !=
            "fictional-development-tasks-ai-review-not-human-gold"
            or len(cases) != 6 or len({c["id"] for c in cases}) != 6
            or {c["kind"] for c in cases} !=
            {"plan", "feedback", "uncertain", "conflict", "fault"}):
        raise ValueError("Unknown/faulty learning task fixture")
    return cases


class CampaignSDK:
    """Model/embedding allowlist, conservative pre-call reserves, no automatic retries."""
    def __init__(self, sdk, ledger):
        self.sdk, self.ledger = sdk, ledger
        self.records, self.failed = [], False
        self.responses = SimpleNamespace(create=self.respond)
        self.embeddings = SimpleNamespace(create=self.embed)

    def respond(self, **kwargs):
        if self.failed:
            raise BudgetExceeded("Campaign stopped after an uncertain provider request")
        tools = kwargs.get("tools", [])
        if (kwargs.get("model") != "gpt-6-luna" or len(json.dumps(kwargs).encode()) > 50_000
                or any(t.get("type") != "function" or t.get("name") not in ALLOWED_TOOLS
                       for t in tools)):
            raise ValueError("Unapproved model, request size or tool")
        kwargs.update(store=False, service_tier="default", reasoning={"effort": "none"})
        kwargs["max_output_tokens"] = min(kwargs.get("max_output_tokens", 1500), 6000)
        self.ledger.reserve(0.02)
        try:
            response = self.sdk.responses.create(**kwargs)
            if response.usage is None:
                raise BudgetExceeded("Missing usage")
            actual = (response.usage.input_tokens * .1 + response.usage.output_tokens * .5) / 1e6
            self.ledger.settle(0.02, actual)
        except Exception:
            self.failed = True
            raise
        self.records.append({"input": kwargs["input"],
                             "schema": kwargs.get("text", {}).get("format", {}).get("name"),
                             "output": [item.model_dump(mode="json") for item in response.output],
                             "output_text": response.output_text, "status": response.status})
        return response

    def embed(self, **kwargs):
        if (self.failed or kwargs.get("model") != "text-embedding-3-small"
                or len(json.dumps(kwargs).encode()) > 30_000):
            raise ValueError("Campaign stopped or unapproved embedding request")
        self.ledger.reserve(0.001)
        try:
            response = self.sdk.embeddings.create(**kwargs)
            if response.usage is None:
                raise BudgetExceeded("Missing embedding usage")
            self.ledger.settle(0.001, response.usage.total_tokens * .02 / 1e6)
        except Exception:
            self.failed = True
            raise
        return response


class ChallengeBase(HybridKnowledgeBase):
    """Same canonical ranker/reranker/generator; only exposes explicit ablation modes."""
    def retrieve(self, course, material_type, question, rerank=None):
        started = time.perf_counter()
        rows = self._current_rows(self.store.rows(self.signature, course, material_type))
        vector = [] if self.variant == "bm25" else self.query_vectors[question]
        mode = "hybrid" if self.variant == "hybrid-rerank" else self.variant
        candidates = rank_candidates(question, rows, vector, self.config.rag_candidate_count, mode)
        self.candidate_ids = [r["chunk_id"] for r in candidates]
        if self.variant == "hybrid-rerank" and candidates:
            candidates = self._rerank(question, candidates)
            candidates = [r for r in candidates if r["rerank_score"] >= 2]
        result = self._current_rows(candidates)[:self.config.max_search_results]
        self.retrieval_ms = round((time.perf_counter() - started) * 1000, 3)
        return result


def averages(records):
    completed = [r for r in records if "response" in r]
    metrics = ("recall_at_3", "precision_at_3", "mrr_at_3", "ndcg_at_3")
    result = {"planned": 24, "completed": len(completed), "attempted": len(records),
              "not_attempted": 24 - len(records), "coverage": len(completed) / 24}
    for key in metrics:
        values = [r["metrics"][key] for r in completed if r["metrics"][key] is not None]
        result[key] = {"mean": mean(values) if values else None, "denominator": len(values)}
    result["cross_course_count"] = sum(r["metrics"]["cross_course_count"] for r in completed)
    result["archived_negative_hits"] = sum(r["metrics"]["archived_negative_hits"]
                                           for r in completed)
    result["generation_diagnostics"] = {
        reason: sum(r["response"]["diagnostics"]["reason"] == reason for r in completed)
        for reason in sorted({r["response"]["diagnostics"]["reason"] for r in completed})}
    times = sorted(r["response"]["latency_ms"] for r in completed)
    result["p50_ms_excluding_query_embedding"] = times[(len(times) - 1) // 2] if times else None
    result["p95_ms_excluding_query_embedding"] = (
        times[math.ceil(len(times) * .95) - 1] if times else None)
    result["mean_retrieval_ms"] = mean(r["retrieval_ms"] for r in completed) if completed else None
    result["model_semantic_accuracy"] = None
    return result


def run_rag(config, client, data, passages, root, report, checkpoint):
    cases = [case for case in data["cases"] if case["split"] == "dev"]
    query_path = root / "query-vectors.json"
    if query_path.exists():
        cache = json.loads(query_path.read_text())
        if cache["questions_hash"] != digest([c["question"] for c in cases]):
            raise ValueError("Query embedding cache mismatch")
        query_vectors = cache["vectors"]
    else:
        embedder = HybridKnowledgeBase(config, client)
        values = embedder._embed([c["question"] for c in cases])
        query_vectors = dict(zip((c["question"] for c in cases), values))
        save_state(query_path, {"questions_hash": digest([c["question"] for c in cases]),
                                "vectors": query_vectors})
    base = ChallengeBase(config, client)
    base.query_vectors = query_vectors
    lookup = {chunk_id(key): key for key in passages}
    for variant in VARIANTS:
        base.variant = variant
        for case in cases:
            key = f"rag:{variant}:{case['id']}"
            if key in report["records"]:
                continue
            before = client.ledger.data["known_cost_usd"]
            trace_start = len(client.records)
            try:
                response = base.chat(case["course"], "all", case["question"])
                selected = [lookup[e.chunk_id] for e in response.evidence]
                result = {"key": key, "kind": "rag", "variant": variant,
                          "case_id": case["id"], "category": case["category"],
                          "question": case["question"], "response": response.model_dump(),
                          "selected_passages": selected,
                          "candidate_passages": [lookup[k] for k in base.candidate_ids],
                          "metrics": passage_metrics(case, selected, passages),
                          "retrieval_ms": base.retrieval_ms,
                          "call_cost_usd": client.ledger.data["known_cost_usd"] - before,
                          "review_dimensions": ["correct", "supported"],
                          "raw_model_turns": client.records[trace_start:]}
            except Exception as exc:
                report["records"][key] = {"key": key, "kind": "rag", "variant": variant,
                                           "case_id": case["id"], "error_type": type(exc).__name__}
                checkpoint()
                raise
            report["records"][key] = result
            checkpoint()
            print(json.dumps({"phase": "rag", "variant": variant, "case_id": case["id"],
                              "recall_at_3": result["metrics"]["recall_at_3"],
                              "reason": response.diagnostics.reason,
                              "accounted_usd": client.ledger.data["accounted_usd"]}), flush=True)


def fixture_plan(store, config, case, passages):
    request = PlanRequest(request_id=uuid.uuid5(uuid.NAMESPACE_URL, "challenge-seed:" + case["id"]),
                          course="LAB202", goal="Fictional feedback evaluation seed", days=3,
                          daily_minutes=30)
    state, fresh = store.start(request)
    if not fresh:
        return store.get(state["id"])
    tasks = []
    for day, key in enumerate(("foundations:rag", "retrieval:lexical", "citations:support"), 1):
        passage = passages[key]
        document = passage["document"]
        relative = f"LAB202/{document['material_type']}/{document['id']}.md"
        quote = passage["text"].split(". ")[0] + "."
        tasks.append({"id": uuid.uuid4().hex, "day": day, "minutes": 30, "done": False,
                      "title": f"Fictional review day {day}", "study_goal": passage["text"][:180],
                      "quiz_question": "How do keyword and semantic retrieval differ?" if day == 2
                      else "Explain the quoted course principle.", "quiz_answer": passage["text"],
                      "citations": [{"source_id": "S1", "chunk_id": chunk_id(key),
                                     "filename": f"{document['id']}.md", "path": relative,
                                     "page": 1, "locator_type": "section", "quote": quote}]})
    if case["kind"] == "uncertain":
        # Deliberately unsupported prior-AI quiz: feedback must not trust this seed answer.
        tasks[1].update(quiz_question="What is the LAB202 instructor's exact office?",
                        quiz_answer="Room Cedar-41", study_goal="Verify an unsupported prior quiz")
    plan = store.finish(state["id"], "draft", {"request": request.model_dump(mode="json"),
                        "title": "SCRIPTED seed, not a model-generated plan", "tasks": tasks,
                        "trace": []})
    base = HybridKnowledgeBase(config)
    plan = store.confirm(plan["id"], plan["version"],
                         verifier=lambda p: check_plan(p, base.source))
    return store.set_task(plan["id"], plan["tasks"][0]["id"], True, plan["version"])


def run_agents(config, client, passages, root, report, checkpoint):
    store = StudyStore(root / "learning-tasks.sqlite3")
    for case in load_tasks():
        key = f"agent:{case['id']}"
        if key in report["records"]:
            continue
        before, trace_start = client.ledger.data["known_cost_usd"], len(client.records)
        controls = {}
        if case["kind"] == "plan":
            request = PlanRequest(request_id=uuid.uuid5(uuid.NAMESPACE_URL, key), course="LAB202",
                                  goal=case["goal"], days=case["days"], daily_minutes=30)
            plan = StudyAgent(config, store, client).run(request)
            controls["draft_not_auto_confirmed"] = plan["status"] != "active"
            original = plan
            if plan["status"] == "draft":
                base = HybridKnowledgeBase(config)
                plan = store.confirm(plan["id"], plan["version"],
                                     verifier=lambda p, source=base.source: check_plan(p, source))
                controls["scripted_confirmation_no_calls"] = (
                    plan["api_calls"] == original["api_calls"])
            controls["delivered_plan"] = plan["status"] == "active"
            outcome = {"plan": original, "scripted_confirmed_state": plan}
        else:
            plan = fixture_plan(store, config, case, passages)
            original = json.loads(json.dumps(plan))
            request = FeedbackRequest(request_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
                                      version=plan["version"], answer=case["student_answer"])
            actual_retrieve = HybridKnowledgeBase.retrieve

            def retrieve(base, *args, _kind=case["kind"], _plan=plan,
                         _retrieve=actual_retrieve, **kwargs):
                if _kind == "fault":
                    raise RuntimeError("Injected fictional retrieval failure")
                rows = _retrieve(base, *args, **kwargs)
                if _kind == "conflict":
                    current = store.get(_plan["id"])
                    store.set_task(_plan["id"], _plan["tasks"][2]["id"], True, current["version"])
                return rows

            with patch.object(HybridKnowledgeBase, "retrieve", retrieve):
                result = FeedbackAgent(config, store, client).run(
                    plan["id"], plan["tasks"][1]["id"], request)
            feedback = result["feedback"][-1]
            controls["no_automatic_task_edits"] = result["tasks"] == original["tasks"]
            if case["kind"] not in {"conflict", "fault"}:
                controls["delivered_feedback"] = feedback["status"] == "draft"
            if case["kind"] == "conflict":
                controls = {"stale_proposal_not_draft": feedback["status"] == "failed",
                            "late_progress_preserved": result["tasks"][2]["done"],
                            "completed_content_preserved": (
                                result["tasks"][0] == original["tasks"][0])}
            elif case["kind"] == "fault":
                controls["failure_recorded"] = feedback["status"] == "failed"
                controls["no_automatic_provider_retry"] = len(client.records) - trace_start == 1
            elif feedback["status"] == "draft":
                controls["verdict_matches_draft_rubric"] = (
                    feedback["assessment"]["verdict"] == case["expected_verdict"])
                if case["id"] == "misconception":
                    controls["later_task_adjustment_proposed"] = bool(feedback.get("adjustments"))
                if feedback.get("adjustments"):
                    base = HybridKnowledgeBase(config)
                    result = store.confirm_feedback(plan["id"], feedback["id"], result["version"],
                        verifier=lambda p, f, source=base.source: check_feedback(p, f, source))
                    controls["completed_content_preserved"] = (
                        result["tasks"][0] == original["tasks"][0])
                    calls = client.ledger.data["calls"]
                    try:
                        store.confirm_feedback(plan["id"], feedback["id"], original["version"])
                    except StudyConflict:
                        pass
                    controls["replay_no_model_calls"] = client.ledger.data["calls"] == calls
            outcome = {"seed_plan": original, "returned_plan": result, "feedback": feedback}
        record = {"key": key, "kind": "agent", "case_id": case["id"],
                  "scenario_kind": case["kind"], "rubric": case["rubric"],
                  "review_dimensions": case["review_dimensions"], "controls": controls,
                  "outcome": outcome, "raw_model_turns": client.records[trace_start:],
                  "call_cost_usd": client.ledger.data["known_cost_usd"] - before}
        report["records"][key] = record
        checkpoint()
        print(json.dumps({"phase": "agent", "case_id": case["id"], "controls": controls,
                          "accounted_usd": client.ledger.data["accounted_usd"]}), flush=True)
        if client.failed:
            raise BudgetExceeded("Provider request uncertain; campaign stopped")


def run(root, budget, phase, budget_root):
    data, passages = load_dataset()
    settings = get_settings()
    if settings.openai_model != "gpt-6-luna":
        raise ValueError("Preserve approved model")
    config = Settings(_env_file=None, openai_api_key=settings.openai_api_key,
                      openai_model="gpt-6-luna", openai_max_retries=0,
                      knowledge_base_dir=root / "knowledge", rag_db_path=root / "index.sqlite3",
                      telemetry_path=root / "telemetry.jsonl", max_search_results=3,
                      rag_candidate_count=10, rag_rerank=False, agent_run_budget_usd=.05,
                      agent_daily_budget_usd=.50)
    metadata = {"dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                "tasks_sha256": hashlib.sha256(TASKS.read_bytes()).hexdigest(),
                "implementation_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sorted(
                    (PROJECT_ROOT / "backend/app").rglob("*.py")))).hexdigest(),
                "model": config.openai_model, "split": "dev", "label_status": "draft_ai_authored",
                "representation": "matched-raw-passage-text-for-dense-and-bm25",
                "max_results": 3, "candidate_count": 10,
                "timing": "embedding preprocessing excluded from timing, included in campaign cost"}
    fingerprint = digest(metadata)
    path = root / "campaign.json"
    report = json.loads(path.read_text()) if path.exists() else {
        "fingerprint": fingerprint, "metadata": metadata, "records": {}, "human_reviews": 0}
    if report["fingerprint"] != fingerprint:
        raise ValueError("Experiment changed; never overwrite or silently spend a new budget")
    root.mkdir(parents=True, exist_ok=True)
    ledger = BudgetLedger(budget_root / "budget.json", budget)
    with ledger.lock():
        if ledger.data["uncertain_calls"]:
            raise BudgetExceeded("Uncertain charges require operator audit before continuing")
        sdk = OpenAI(api_key=config.openai_api_key, max_retries=0, timeout=20)
        client = CampaignSDK(sdk, ledger)
        report.setdefault("accounted_usd_at_start", ledger.data["accounted_usd"])

        def checkpoint():
            report["budget"] = dict(ledger.data)
            report["rag_summary"] = {variant: averages([
                r for r in report["records"].values() if r.get("variant") == variant])
                for variant in VARIANTS}
            save_state(path, report)

        checkpoint()
        try:
            materialize(config, data, passages, client, root)
            if phase in {"rag", "both"}:
                run_rag(config, client, data, passages, root, report, checkpoint)
            if phase in {"agent", "both"}:
                run_agents(config, client, passages, root, report, checkpoint)
        except Exception as exc:
            report["stopped_error_type"] = type(exc).__name__
            checkpoint()
            raise
        checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--phase", choices=("rag", "agent", "both"), default="both")
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "evals/reports/challenge-v1-20261025/matched-v1")
    parser.add_argument("--budget-root", type=Path,
                        default=PROJECT_ROOT / "evals/reports/challenge-v1-20261025")
    parser.add_argument("--budget-usd", type=float, default=.50)
    args = parser.parse_args()
    data, _ = load_dataset()
    tasks = load_tasks()
    if not args.live:
        print(json.dumps({"documents": len(data["documents"]), "passages": 40,
                          "dev_questions": 24, "frozen_holdout": 8, "learning_tasks": len(tasks),
                          "api_calls": 0}))
        return
    root = (PROJECT_ROOT / "evals/reports").resolve()
    if (args.budget_root.resolve() == root or not args.budget_root.resolve().is_relative_to(root)
            or not args.output.resolve().is_relative_to(args.budget_root.resolve())):
        parser.error("Run must stay under its ignored shared campaign-budget directory")
    run(args.output.resolve(), args.budget_usd, args.phase, args.budget_root.resolve())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"stopped": True, "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None

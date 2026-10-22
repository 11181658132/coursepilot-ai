"""Explicit, bounded live smoke: fictional RAG + plan + feedback + deletion.

Not a quality benchmark. Raw results are restricted to a fresh ignored directory.
"""

import argparse
import json
import time
import uuid
from pathlib import Path

from openai import OpenAI

from app.benchmark import prepare_hybrid
from app.config import PROJECT_ROOT, get_settings
from app.eval_budget import BudgetClient, BudgetExceeded, BudgetLedger
from app.services.state import save_state
from app.services.study_agent import PlanRequest, StudyAgent
from app.services.study_feedback import FeedbackAgent, FeedbackRequest
from app.services.study_store import StudyStore


class AcceptanceClient(BudgetClient):
    def respond(self, **kwargs):
        tools = kwargs.get("tools", [])
        if not tools:
            return super().respond(**kwargs)
        allowed = {"list_materials", "search_knowledge", "propose_study_plan", "propose_feedback"}
        if (
            kwargs.get("model") != "gpt-6-luna"
            or any(t.get("type") != "function" or t.get("name") not in allowed for t in tools)
            or len(json.dumps(kwargs).encode()) > 50_000
        ):
            raise ValueError("Unbudgeted live request")
        kwargs.update(
            max_output_tokens=min(kwargs.get("max_output_tokens", 6000), 6000),
            store=False,
            service_tier="default",
            reasoning={"effort": "none"},
        )
        self.ledger.reserve(0.02)
        response = self.client.responses.create(**kwargs)
        if response.usage is None:
            raise BudgetExceeded("Missing usage; reservation retained")
        actual = (response.usage.input_tokens * 0.1 + response.usage.output_tokens * 0.5) / 1e6
        self.ledger.settle(0.02, actual)
        return response


def verified(citations, base):
    return bool(citations) and all(
        (source := base.source(c["chunk_id"])) is not None
        and " ".join(c["quote"].split()) in " ".join(source["text"].split())
        for c in citations
    )


def accept(directory, budget):
    target = directory.resolve()
    if not target.is_relative_to((PROJECT_ROOT / "evals/reports").resolve()) or target.exists():
        raise ValueError("Use a fresh ignored evals/reports directory")
    if not 0 < budget <= 0.05:
        raise ValueError("Live acceptance budget must not exceed USD .05")
    config = get_settings().model_copy(
        update={"openai_max_retries": 0, "agent_request_timeout_seconds": 20}
    )
    if config.openai_model != "gpt-6-luna" or not config.openai_api_key:
        raise ValueError("Existing approved model/key required")
    ledger = BudgetLedger(target / "budget.json", budget)
    checks, raw = {}, {}
    started = time.perf_counter()
    with ledger.lock():
        client = AcceptanceClient(
            OpenAI(api_key=config.openai_api_key, max_retries=0, timeout=45), ledger
        )
        try:
            base = prepare_hybrid(config, client, target / "runtime")
            for name, question in (
                ("rag_answer", "Why are citations useful in a RAG system?"),
                ("rag_refusal", "What is the instructor's private phone number?"),
            ):
                result = base.chat("DEMO101", "all", question)
                raw[name] = result.model_dump()
                checks[name] = (
                    verified([c.model_dump() for c in result.citations], base)
                    if name == "rag_answer"
                    else not result.citations
                    and result.diagnostics.reason
                    in {"no_relevant_evidence", "generation_no_citations"}
                )
                if not checks[name]:
                    raise ValueError("RAG acceptance failed; no automatic retry")
            store = StudyStore(target / "agent.sqlite3")
            request = PlanRequest(
                request_id=uuid.uuid4(),
                course="DEMO101",
                days=3,
                goal="Create a 3-day plan on retrieval, citations and evaluation. On day 1 "
                "focus on whether matching keywords proves an answer is supported.",
            )
            draft = StudyAgent(base.config, store, client=client).run(request)
            raw["draft"] = draft
            checks["plan_draft"] = draft["status"] == "draft" and len(draft["tasks"]) == 3
            if not checks["plan_draft"]:
                raise ValueError("Plan acceptance failed; no automatic retry")
            checks["plan_quotes"] = all(verified(t["citations"], base) for t in draft["tasks"])
            plan = store.confirm(draft["id"], draft["version"])
            plan = store.set_task(plan["id"], plan["tasks"][0]["id"], True, plan["version"])
            before = json.loads(json.dumps(plan["tasks"]))
            feedback = FeedbackAgent(base.config, store, client=client).run(
                plan["id"],
                plan["tasks"][0]["id"],
                FeedbackRequest(
                    request_id=uuid.uuid4(),
                    version=plan["version"],
                    answer="Any overlapping keyword proves correctness. "
                    "A real citation guarantees every claim is true. We need not verify support.",
                ),
            )
            raw["feedback"] = feedback
            entry = feedback["feedback"][-1]
            checks["no_automatic_edits"] = feedback["tasks"] == before
            checks["feedback_draft"] = entry["status"] == "draft"
            checks["weakness_proposal"] = entry.get("assessment", {}).get(
                "verdict"
            ) == "needs_review" and bool(entry.get("adjustments"))
            if not checks["feedback_draft"] or not checks["weakness_proposal"]:
                raise ValueError("Feedback acceptance incomplete; no forced proposal or retry")
            checks["feedback_quotes"] = verified(entry["assessment"]["citations"], base)
            applied = store.confirm_feedback(plan["id"], entry["id"], feedback["version"])
            raw["applied"] = applied
            checks["human_confirmed_edits"] = applied["tasks"] != before
            checks["progress_preserved"] = applied["tasks"][0] == before[0]
            checks["persistent_reload"] = StudyStore(store.path).get(plan["id"]) == applied
            store.delete(plan["id"], applied["version"])
            try:
                store.get(plan["id"])
                checks["deleted_content_unreadable"] = False
            except KeyError:
                checks["deleted_content_unreadable"] = True
            with store.connect() as db:
                checks["billing_retained"] = (
                    db.execute("SELECT COUNT(*) FROM study_charges").fetchone()[0] > 0
                )
        except Exception as exc:
            raw["stopped_error_type"] = type(exc).__name__
        summary = {
            "checks": checks,
            "passed": bool(checks) and all(checks.values()) and "stopped_error_type" not in raw,
            "budget": dict(ledger.data),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "semantic_accuracy": None,
            "human_review_count": 0,
            "scope": "five fictional DEMO101 files; local service acceptance",
        }
        save_state(target / "results.json", raw)
        save_state(target / "summary.json", summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=0.05)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"api_calls": 0, "requires_explicit_live_flag": True}))
        return
    try:
        print(json.dumps(accept(args.output_dir, args.budget_usd)))
    except Exception as exc:
        print(json.dumps({"stopped_error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

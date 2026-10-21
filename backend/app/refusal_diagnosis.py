"""Explicit opt-in reproduction of historical public-corpus no-citation failures.

Only fictional data; raw provider text stays in ignored local diagnostic reports.
This is targeted development diagnosis, not an independent quality benchmark.
"""

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace

from openai import OpenAI

from app.benchmark import DATASET, corpus_hash, prepare_hybrid, validate_dataset
from app.config import PROJECT_ROOT, get_settings
from app.eval_budget import BudgetClient, BudgetLedger
from app.evaluation import score_case
from app.models import Evidence
from app.services.hybrid_kb import validate_answer
from app.services.state import save_state


class TraceClient:
    def __init__(self, budget_client):
        self.client = budget_client
        self.records = []
        self.embeddings = budget_client.embeddings
        self.responses = SimpleNamespace(create=self.respond)

    def respond(self, **kwargs):
        response = self.client.responses.create(**kwargs)
        self.records.append({"schema": kwargs["text"]["format"]["name"],
                             "status": response.status, "output_text": response.output_text})
        return response


def selected_cases():
    cases = {case.id: case for case in validate_dataset()}
    selection = []
    for variant in ("hybrid-no-rerank", "hybrid"):
        path = PROJECT_ROOT / "evals/reports/public-benchmark" / variant / "latest.json"
        report = json.loads(path.read_text())
        if report["metadata"]["dataset_sha256"] != hashlib.sha256(DATASET.read_bytes()).hexdigest():
            raise ValueError("Historical dataset no longer matches; do not silently change cases")
        if report["metadata"]["corpus_sha256"] != corpus_hash():
            raise ValueError("Historical corpus changed")
        for row in report["results"]:
            case = cases[row["id"]]
            if not row["citations"] and not row["error"] and row["evidence"]:
                if case.course != "DEMO101" or case.split != "dev":
                    raise ValueError("No private or holdout data may enter this diagnosis")
                selection.append((variant, case))
    return selection


def replay(root):
    """Revalidate identical saved drafts, not new generations or a benchmark rerun."""
    source = root / "baseline/latest.json"
    report = json.loads(source.read_text())
    metadata = report["metadata"]
    if (metadata["dataset"] != hashlib.sha256(DATASET.read_bytes()).hexdigest()
            or metadata["corpus"] != corpus_hash()):
        raise ValueError("Replay corpus/dataset must match the immutable baseline")
    results = []
    for row in report["results"]:
        outputs = [item for item in row["raw_model_outputs"]
                   if item["schema"] == "grounded_answer"]
        if not outputs or outputs[-1]["status"] != "completed":
            results.append({"id": row["id"], "variant": row["variant"], "replayed": False})
            continue
        try:
            draft = json.loads(outputs[-1]["output_text"])
        except json.JSONDecodeError:
            results.append({"id": row["id"], "variant": row["variant"], "replayed": False})
            continue
        _, _, diagnostic = validate_answer(draft, [Evidence(**e) for e in row["evidence"]])
        results.append({"id": row["id"], "variant": row["variant"], "replayed": True,
                        "original_reason": row["diagnostics"]["reason"],
                        **diagnostic.model_dump()})
    output = {"warning": "Selected development drafts only; not full benchmark accuracy",
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "validator_sha256": hashlib.sha256(
                  (PROJECT_ROOT / "backend/app/services/hybrid_kb.py").read_bytes()).hexdigest(),
              "api_calls": 0, "results": results,
              "reason_counts": dict(Counter(row.get("reason", "not_replayed") for row in results)),
              "markers_repaired": sum(row.get("citation_markers_repaired", False)
                                      for row in results)}
    save_state(root / "replay/latest.json", output)
    print(json.dumps({key: value for key, value in output.items() if key != "results"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", action="store_true")
    parser.add_argument("--phase", choices=("baseline", "after-fix"), default="baseline")
    parser.add_argument("--budget-usd", type=float, default=0.05)
    args = parser.parse_args()
    root = PROJECT_ROOT / "evals/reports/refusal-diagnosis"
    if args.replay:
        replay(root)
        return
    if not 0 < args.budget_usd <= 0.05:
        parser.error("Diagnosis campaign must stay within the separately approved USD 0.05")
    selection = selected_cases()
    if not args.live:
        print(json.dumps({"selected_variant_cases": len(selection), "api_calls": 0}))
        return
    ledger = BudgetLedger(root / "budget.json", args.budget_usd)
    with ledger.lock():
        config = get_settings()
        if config.openai_model != "gpt-6-luna":
            raise ValueError("Preserve the approved model")
        sdk = OpenAI(api_key=config.openai_api_key, max_retries=0, timeout=45)
        client = TraceClient(BudgetClient(sdk, ledger))
        base = prepare_hybrid(config, client, root / "runtime")
        path = root / args.phase / "latest.json"
        metadata = {"phase": args.phase,
                    "dataset": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                    "corpus": corpus_hash(), "model": config.openai_model,
                    "code": hashlib.sha256(b"".join(path.read_bytes() for path in sorted(
                        (PROJECT_ROOT / "backend/app").rglob("*.py")))).hexdigest()}
        report = json.loads(path.read_text()) if path.exists() else {
            "metadata": metadata, "warning": "Targeted reproduction; not full benchmark accuracy",
            "results": []}
        if report["metadata"] != metadata:
            raise ValueError("Diagnostic phase configuration changed; do not overwrite results")
        done = {(row["variant"], row["id"]) for row in report["results"]}
        for variant, case in selection:
            if (variant, case.id) in done:
                continue
            client.records = []
            base.config.rag_rerank = variant == "hybrid"
            response = base.chat(case.course, case.material_type, case.question)
            result = asdict(score_case(case, response))
            result.update(variant=variant, diagnostics=response.diagnostics.model_dump(),
                          raw_model_outputs=client.records)
            report["results"].append(result)
            report["budget"] = dict(ledger.data)
            report["reason_counts"] = dict(Counter(
                row["diagnostics"]["reason"] for row in report["results"]))
            save_state(path, report)
            print(json.dumps({"completed": len(report["results"]), "variant": variant,
                              "reason": response.diagnostics.reason,
                              "accounted_usd": round(ledger.data["accounted_usd"], 8)}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"stopped": True, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None

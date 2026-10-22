"""One-shot, explicitly budgeted fictional regression. Not a benchmark rerun."""

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from openai import OpenAI

from app.benchmark import DATASET, corpus_hash, prepare_hybrid, validate_dataset
from app.config import PROJECT_ROOT, get_settings
from app.eval_budget import BudgetClient, BudgetLedger
from app.evaluation import score_case
from app.services.state import save_state

VARIANTS = ("hybrid-no-rerank", "hybrid")


def selected_case(directory):
    """Verify the exact two failed development outputs, never select holdout/private data."""
    case = next(c for c in validate_dataset() if c.id == "public-30")
    if case.split != "dev" or case.course != "DEMO101":
        raise ValueError("Only the approved fictional development case is allowed")
    sources, fingerprints = {}, set()
    for variant in VARIANTS:
        path = directory / variant / "latest.json"
        report = json.loads(path.read_text())
        metadata = report["metadata"]
        if (report["variant"] != variant or metadata["split"] != "dev"
                or metadata["corpus_sha256"] != corpus_hash()
                or metadata["dataset_sha256"] != hashlib.sha256(DATASET.read_bytes()).hexdigest()):
            raise ValueError("Require unchanged fictional development reports")
        rows = [row for row in report["results"] if row["id"] == case.id]
        if (len(rows) != 1 or rows[0]["question"] != case.question or rows[0].get("error")
                or rows[0]["course"] != "DEMO101" or rows[0]["split"] != "dev"
                or (rows[0].get("diagnostics") or {}).get("reason") != "quote_not_found"):
            raise ValueError("Require the original quote-validation failure")
        fingerprints.add(report["fingerprint"])
        sources[variant] = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(fingerprints) != 1:
        raise ValueError("Cannot diagnose different source experiments")
    return case, sources


class FictionalTraceClient:
    def __init__(self, client):
        self.client, self.records = client, []
        self.embeddings = client.embeddings
        self.responses = SimpleNamespace(create=self.respond)

    def respond(self, **kwargs):
        response = self.client.responses.create(**kwargs)
        self.records.append({"schema_name": kwargs["text"]["format"]["name"],
                             "schema": kwargs["text"]["format"]["schema"],
                             "input": json.loads(kwargs["input"]),
                             "status": response.status, "output_text": response.output_text})
        return response


def run(directory, output, budget):
    case, sources = selected_case(directory)
    config = get_settings().model_copy(update={"openai_max_retries": 0})
    if config.openai_model != "gpt-6-luna":
        raise ValueError("Keep the approved model")
    # Never retry a completed outcome for a better score.
    output.mkdir(parents=True, exist_ok=False)
    ledger = BudgetLedger(output / "budget.json", budget)
    with ledger.lock():
        sdk = OpenAI(api_key=config.openai_api_key, max_retries=0, timeout=45)
        trace = FictionalTraceClient(BudgetClient(sdk, ledger))
        base = prepare_hybrid(config, trace, output / "runtime")
        report = {"mode": "targeted-fictional-development-acceptance", "case_id": case.id,
                  "source_report_hashes": sources, "corpus_sha256": corpus_hash(),
                  "implementation_sha256": hashlib.sha256(b"".join(
                      p.read_bytes() for p in sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
                  )).hexdigest(), "results": [], "human_reviewed": 0}
        for variant in VARIANTS:
            trace.records = []
            base.config.rag_rerank = variant == "hybrid"
            try:
                result = base.chat(case.course, case.material_type, case.question)
            except Exception as exc:
                report["stopped_error_type"] = type(exc).__name__
                report["budget"] = dict(ledger.data)
                save_state(output / "report.json", report)
                raise
            report["results"].append({"variant": variant,
                                      "result": asdict(score_case(case, result)),
                                      "trace": trace.records})
            report["budget"] = dict(ledger.data)
            save_state(output / "report.json", report)
            print(json.dumps({"variant": variant, "reason": result.diagnostics.reason,
                              "accounted_usd": ledger.data["accounted_usd"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--directory", type=Path,
                        default=PROJECT_ROOT / "evals/reports/public-benchmark-20261022")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-usd", type=float, default=0.05)
    args = parser.parse_args()
    root = (PROJECT_ROOT / "evals/reports").resolve()
    if (args.output.resolve() == root or not args.output.resolve().is_relative_to(root)
            or not 0 < args.budget_usd <= 0.05):
        parser.error("Require ignored report subdirectory and approved budget <= $0.05")
    selected_case(args.directory)
    if not args.live:
        print(json.dumps({"selected_variant_cases": 2, "api_calls": 0}))
        return
    run(args.directory, args.output, args.budget_usd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"stopped": True, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None

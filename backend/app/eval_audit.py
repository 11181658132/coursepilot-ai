"""Offline regrading of cached fictional responses; performs zero API calls.

v3 aligns quote whitespace handling with the product and reconciles legacy hosted
report costs. Original v2 reports are preserved, never silently overwritten.
"""

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from app.benchmark import DATASET, VARIANTS, compare, corpus_hash, validate_dataset
from app.eval_budget import BudgetLedger
from app.evaluation import SCORING_VERSION, EvalResult, score_case, summarise, write_report
from app.models import ChatResponse
from app.services.state import save_state


def _audit_suite(directory: Path):
    cases = {case.id: case for case in validate_dataset()}
    reports = {variant: json.loads((directory / variant / "latest.json").read_text())
               for variant in VARIANTS}
    dataset_hash = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    if len({report["fingerprint"] for report in reports.values()}) != 1:
        raise ValueError("Cannot regrade mismatched experiments")
    if any(report["metadata"]["dataset_sha256"] != dataset_hash for report in reports.values()):
        raise ValueError("Dataset changed; cached responses must not be relabelled silently")
    if any(report["metadata"]["corpus_sha256"] != corpus_hash() for report in reports.values()):
        raise ValueError("Corpus changed since responses were generated")
    if any([row["id"] for row in report["results"]] != report["metadata"]["case_ids"]
           for report in reports.values()):
        raise ValueError("Incomplete experiment; wait until all planned cases finish")
    if any(report.get("scoring_version") != 2 for report in reports.values()):
        raise ValueError("This migration applies only once to original v2 reports")
    if any(report["summary"]["blocked"] for report in reports.values()):
        raise ValueError("Finish or separately audit blocked/uncertain requests first")
    ledger = json.loads((directory / "budget.json").read_text())
    if ledger["uncertain_calls"]:
        raise ValueError("An API call is still running or has unknown charges")
    original_fingerprint = reports["hybrid"]["fingerprint"]
    audit = {"source_fingerprint": original_fingerprint, "scoring_version": SCORING_VERSION,
             "grading_code_sha256": hashlib.sha256(
                 Path(__file__).read_bytes()
                 + Path(__file__).with_name("evaluation.py").read_bytes()).hexdigest(),
             "changes": ["quote_whitespace_normalization", "hosted_usage_cost_reconciliation"],
             "generation_rerun": False, "additional_api_calls": 0}
    fingerprint = hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest()
    total = 0.0
    for variant, report in reports.items():
        updated = []
        for row in report["results"]:
            case = cases[row["id"]]
            if row["question"] != case.question:
                raise ValueError("Cached question no longer matches the reference")
            usage = dict(row["usage"])
            if variant == "file_search":
                usage["file_search_calls"] = 1  # Required single search in v2 runner.
            cost = (usage.get("input_tokens", 0) * .10
                    + usage.get("output_tokens", 0) * .50) / 1_000_000
            cost += usage.get("embedding_tokens", 0) * .02 / 1_000_000
            cost += usage.get("file_search_calls", 0) * .0025
            response = ChatResponse(answer=row["answer"], citations=row["citations"],
                                    evidence=row["evidence"], latency_ms=row["latency_ms"],
                                    usage=usage, estimated_cost_usd=round(cost, 8))
            updated.append(asdict(score_case(case, response)))
            total += cost
        report["results"] = updated
        report["summary"] = summarise(EvalResult(**row) for row in updated)
        report["scoring_version"] = SCORING_VERSION
        report["grading_audit"] = audit
        report["fingerprint"] = fingerprint
        report["human_review"] = {"reviewed": 0, "faithfulness": None, "correctness": None}
    if abs(total - ledger["known_cost_usd"]) > 0.000005:
        raise ValueError("Cached usage costs do not reconcile to the independent budget ledger")
    # All validations precede writes. Preserve original responses/grades for audit.
    if any((directory / variant / "raw-v2.json").exists() for variant in VARIANTS):
        raise FileExistsError("Original audit snapshot already exists; refusing overwrite")
    for variant in reports:
        original = directory / variant / "raw-v2.json"
        save_state(original, json.loads((directory / variant / "latest.json").read_text()))
    for variant, report in reports.items():
        write_report(report, directory / variant)
    comparison = compare(directory)
    return {"paired_cases": comparison["paired_case_count"], "api_calls": 0,
            "reconciled_cost_usd": round(total, 8), "audit": audit}


def audit_suite(directory: Path):
    # Never race the live runner or make any paid call.
    with BudgetLedger(directory / "budget.json", 0.50).lock():
        return _audit_suite(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_suite(args.directory)))


if __name__ == "__main__":
    main()

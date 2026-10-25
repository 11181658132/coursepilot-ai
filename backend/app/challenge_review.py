"""Hash-bound AI-assisted judgments, never human labels or automatic semantic grades."""

import argparse
import json
from pathlib import Path

from app.challenge_suite import digest
from app.config import PROJECT_ROOT
from app.review_analysis import metric
from app.services.state import save_state


def analyse(report, labels):
    eligible = {key: record for key, record in report["records"].items()
                if record.get("review_dimensions") and not record.get("error_type")}
    reviewed = {}
    for label in labels:
        if not isinstance(label, dict) or set(label) != {
                "key", "record_sha256", "provenance", "scores", "notes"}:
            raise ValueError("Separate AI review schema required; no human attestation")
        key = label["key"]
        if key not in eligible or key in reviewed:
            raise ValueError("Unknown/duplicate review target")
        record = eligible[key]
        if (label["provenance"] != "ai_assisted_not_human"
                or label["record_sha256"] != digest(record)
                or not isinstance(label["scores"], dict)
                or set(label["scores"]) != set(record["review_dimensions"])
                or any(value is not None and type(value) is not bool
                       for value in label["scores"].values())
                or not isinstance(label["notes"], str) or not label["notes"].strip()):
            raise ValueError("Require exact fingerprint, explicit tri-state dimensions and notes")
        reviewed[key] = label
    if set(reviewed) != set(eligible):
        raise ValueError("Incomplete review; never assume omitted outcomes pass")
    groups = {}
    for key, label in reviewed.items():
        record = eligible[key]
        group = f"rag:{record['variant']}" if record["kind"] == "rag" else "agent:semantic"
        groups.setdefault(group, []).append(label)
    result = {}
    for group, rows in groups.items():
        dimensions = sorted({key for row in rows for key in row["scores"]})
        result[group] = {"reviewed": len(rows), "dimensions": {
            key: metric(row["scores"][key] for row in rows
                        if key in row["scores"] and row["scores"][key] is not None)
            for key in dimensions}, "unknown_values": sum(
                value is None for row in rows for value in row["scores"].values())}
    return {"mode": "ai-assisted-development-review-not-human-gold", "human_reviews": 0,
            "fingerprint": report["fingerprint"], "reviewed": len(reviewed), "groups": result,
            "new_api_calls": 0,
            "limitations": ["Single coding-assistant judgments, not calibrated human accuracy.",
                            "Development examples, not held-out or production performance.",
                            "Per-dimension denominators differ; unknown is not a pass."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = (PROJECT_ROOT / "evals/reports").resolve()
    if (args.output.resolve() == root or not args.output.resolve().is_relative_to(root)
            or not args.labels.resolve().is_relative_to(root)
            or not args.report.resolve().is_relative_to(root)):
        parser.error("Raw review artifacts must stay inside ignored evals/reports")
    if args.output.exists():
        parser.error("Never overwrite a review snapshot")
    report = json.loads(args.report.read_text())
    labels = [json.loads(line) for line in args.labels.read_text().splitlines() if line.strip()]
    summary = analyse(report, labels)
    args.output.mkdir(parents=True, exist_ok=False)
    save_state(args.output / "summary.json", summary)
    save_state(args.output / "reviews.json", labels)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Validate offline AI-assisted reviews. Never create human labels or call a model."""

import argparse
import json
from collections import Counter
from pathlib import Path

from app.benchmark import VARIANTS
from app.config import PROJECT_ROOT
from app.quality_review import build_packet, load_suite
from app.review_analysis import metric

FIELDS = {"review_key", "response_hash", "reviewer_kind", "correct", "supported",
          "issues", "notes", "source_checks"}
ISSUES = {"unsupported_detail", "unwarranted_refusal", "language_mismatch",
          "ambiguous_question", "unparsed_citation", "generation_rejected"}


def analyse(packet, mapping, labels):
    rows = {row["review_key"]: row for row in packet["rows"]}
    checked = {}
    for label in labels:
        if not isinstance(label, dict) or set(label) != FIELDS:
            raise ValueError("AI review fields required; human attestation is forbidden")
        key = label["review_key"]
        if key not in rows or key in checked:
            raise ValueError("Unknown or duplicate review key")
        row = rows[key]
        if (label["reviewer_kind"] != "ai_assisted_not_human"
                or label["response_hash"] != row["response_hash"]
                or any(v is not None and type(v) is not bool
                       for v in (label["correct"], label["supported"]))
                or not isinstance(label["notes"], str) or not label["notes"].strip()
                or not isinstance(label["issues"], list)
                or any(issue not in ISSUES for issue in label["issues"])
                or len(set(label["issues"])) != len(label["issues"])
                or not isinstance(label["source_checks"], list) or not label["source_checks"]):
            raise ValueError("Require AI provenance, exact response hash, notes and source checks")
        available = {item.get("filename") for item in row["citations"] + row["evidence"]}
        for anchor in label["source_checks"]:
            if (not isinstance(anchor, dict) or set(anchor) != {"filename", "quote"}
                    or anchor["filename"] not in available
                    or anchor["filename"] not in packet["sources"]
                    or not isinstance(anchor["quote"], str) or len(anchor["quote"]) < 12
                    or " ".join(anchor["quote"].split()) not in
                    " ".join(packet["sources"][anchor["filename"]].split())):
                raise ValueError("Source anchor must occur in an available fictional source")
        checked[key] = label
    if set(checked) != set(rows):
        raise ValueError("Require complete review coverage; never assume missing labels pass")
    variants = {}
    for variant in VARIANTS:
        group = [label for key, label in checked.items() if mapping[key]["variant"] == variant]
        variants[variant] = {
            "reviewed": len(group),
            "correctness": metric(r["correct"] for r in group if r["correct"] is not None),
            "support": metric(r["supported"] for r in group if r["supported"] is not None),
            "both": metric(r["correct"] and r["supported"] for r in group
                           if r["correct"] is not None and r["supported"] is not None),
            "unknown_correctness": sum(r["correct"] is None for r in group),
            "unknown_support": sum(r["supported"] is None for r in group),
            "issues": dict(Counter(issue for r in group for issue in r["issues"])),
        }
    return {"mode": "cached-development-ai-assisted-review", "version": 1,
            "fingerprint": packet["fingerprint"], "reviewed_outputs": len(checked),
            "human_reviewed_outputs": 0, "new_api_calls": 0, "new_api_cost_usd": 0,
            "variants": variants,
            "limitations": ["AI judgments, not independent human labels or gold answers.",
                            "Recorded development fingerprint, not production accuracy.",
                            "Unknown grades are excluded with explicit denominators.",
                            "Source support is not parsed-citation validity or claim attribution.",
                            "No holdout results or causal improvement claim."]}


def run(directory, labels_path, output):
    reports, cases = load_suite(directory)
    packet, mapping = build_packet(reports, cases)
    labels = [json.loads(line) for line in labels_path.read_text().splitlines() if line.strip()]
    summary = analyse(packet, mapping, labels)
    summary["source_reports"] = {
        name: {"generated_at": report.get("generated_at"),
               "metadata": report["metadata"], "automated_summary": report.get("summary")}
        for name, report in reports.items()}
    output.mkdir(parents=True, exist_ok=False)
    (output / "ai-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (output / "ai-reviews.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labels))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=PROJECT_ROOT / "evals/reports/public-benchmark")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = (PROJECT_ROOT / "evals/reports").resolve()
    if (args.output.resolve() == root or not args.output.resolve().is_relative_to(root)
            or not args.labels.resolve().is_relative_to(root)):
        parser.error("AI review labels/output must stay inside ignored evals/reports")
    print(json.dumps(run(args.directory, args.labels, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

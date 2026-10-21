"""Offline development-only RAG triage and blinded human review. No model calls."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import mean

from app.benchmark import DATASET, SOURCES, VARIANTS, corpus_hash
from app.config import PROJECT_ROOT
from app.evaluation import SCORING_VERSION, apply_reviews, load_cases, result_hash

STAGES = ("retrieval", "answer", "citation", "reference_label", "upstream", "none", "uncertain")


def load_suite(directory):
    cases = {case.id: case for case in load_cases(DATASET) if case.split == "dev"}
    dataset_hash = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    reports = {}
    for variant in VARIANTS:
        report = json.loads((directory / variant / "latest.json").read_text())
        metadata = report.get("metadata", {})
        if (report.get("variant") != variant
                or report.get("scoring_version") != SCORING_VERSION
                or metadata.get("dataset_sha256") != dataset_hash
                or metadata.get("corpus_sha256") != corpus_hash()
                or metadata.get("split") != "dev"):
            raise ValueError("Require matching current-corpus development reports and scorer")
        rows = report["results"]
        ids = [row["id"] for row in rows]
        if not ids or len(ids) != len(set(ids)) or ids != metadata.get("case_ids"):
            raise ValueError("Duplicate, missing or unexpected case IDs")
        for row in rows:
            case = cases.get(row["id"])
            if (case is None or row.get("split") != "dev" or row["course"] != "DEMO101"
                    or row["question"] != case.question
                    or row.get("reference_answer") != case.reference_answer):
                raise ValueError("Holdout/private/changed cases cannot enter development review")
        reports[variant] = report
    if len({r.get("fingerprint") for r in reports.values()}) != 1:
        raise ValueError("Cannot pair different experiments")
    if not reports[VARIANTS[0]].get("fingerprint"):
        raise ValueError("Missing experiment fingerprint")
    if len({tuple(row["id"] for row in r["results"]) for r in reports.values()}) != 1:
        raise ValueError("All variants must have identical case coverage/order")
    return reports, cases


def diagnose(reports):
    rows = {name: {r["id"]: r for r in report["results"]}
            for name, report in reports.items()}
    paired = [key for key in rows[VARIANTS[0]]
              if all(not rows[name][key].get("error") for name in VARIANTS)]
    transitions = Counter()
    for key in paired:
        before, after = rows["hybrid-no-rerank"][key], rows["hybrid"][key]
        transitions[f"{int(before['passed'])}->{int(after['passed'])}"] += 1
    return {
        "mode": "offline-development-proxy-triage", "api_calls": 0,
        "paired_completed_cases": len(paired),
        "warning": "Tags are hypotheses, not causal proof or answer accuracy. No new generation.",
        "variants": {name: {
            "cases": len(report["results"]),
            "blocked": sum(bool(r.get("error")) for r in report["results"]),
            "proxy_failures": sum(not r["passed"] and not r.get("error")
                                  for r in report["results"]),
            "tags": dict(Counter(tag for r in report["results"]
                                 for tag in r.get("failure_categories", []))),
        } for name, report in reports.items()},
        "reranking_paired_proxy_transitions": dict(transitions),
        "semantic_correctness": None, "semantic_faithfulness": None,
    }


def build_packet(reports, cases):
    fingerprint = reports[VARIANTS[0]]["fingerprint"]
    # Prioritize cases with proxy failures, but include passes too (avoid failure-only sampling).
    ordered = sorted(cases, key=lambda key: (
        -sum(any(r["id"] == key and (not r["passed"] or r.get("error"))
                 for r in report["results"]) for report in reports.values()), key))
    packet, mapping = [], {}
    for key in ordered:
        candidates = [(name, row) for name, report in reports.items()
                      for row in report["results"] if row["id"] == key and not row.get("error")]
        candidates.sort(key=lambda pair: hashlib.sha256(
            f"{fingerprint}:{key}:{pair[0]}".encode()).hexdigest())
        for variant, row in candidates:
            response_hash = result_hash(reports[variant], row)
            review_key = hashlib.sha256(f"{variant}:{response_hash}".encode()).hexdigest()
            case = cases[key]
            packet.append({"review_key": review_key, "response_hash": response_hash,
                           "case_id": key, "question": row["question"], "answer": row["answer"],
                           "reference_draft": case.reference_answer,
                           "reference_quotes": case.source_quotes,
                           "citations": row.get("citations", []),
                           "evidence": row.get("evidence", [])})
            mapping[review_key] = {"variant": variant, "id": key, "response_hash": response_hash}
    sources = {p.name: p.read_text() for p in sorted(SOURCES.glob("*.md"))}
    return {"fingerprint": fingerprint, "rows": packet, "sources": sources}, mapping


def render_review(packet):
    # Embedded data is inert JSON; escape HTML delimiters to prevent </script> injection.
    data = json.dumps(packet, ensure_ascii=False).replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("&", "\\u0026")
    return REVIEW_HTML.replace("__PACKET__", data)


def prepare(directory, output):
    reports, cases = load_suite(directory)
    packet, mapping = build_packet(reports, cases)
    analysis = diagnose(reports)
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite labels or a historical packet.
    (output / "manifest.json").write_text(json.dumps({
        "fingerprint": packet["fingerprint"], "mapping": mapping,
    }, indent=2))
    (output / "diagnosis.json").write_text(json.dumps(analysis, indent=2))
    (output / "review.html").write_text(render_review(packet), encoding="utf-8")
    lines = ["# Offline RAG development diagnosis", "", analysis["warning"], "",
             "All historical reports are unchanged; no holdout answers or API calls.", "",
             "| Variant | Cases | Blocked | Proxy failures |", "|---|---:|---:|---:|"]
    for variant, values in analysis["variants"].items():
        lines.append(f"| {variant} | {values['cases']} | {values['blocked']} "
                     f"| {values['proxy_failures']} |")
    lines += ["", "## Automated failure tags (overlapping proxy hypotheses)", "",
              "| Tag | File Search | Hybrid no reranker | Hybrid reranker |",
              "|---|---:|---:|---:|"]
    tags = sorted({tag for value in analysis["variants"].values() for tag in value["tags"]})
    for tag in tags:
        counts = [str(analysis["variants"][name]["tags"].get(tag, 0)) for name in VARIANTS]
        lines.append("| " + tag + " | " + " | ".join(counts) + " |")
    lines += ["", "Reranking paired proxy transitions (0=fail, 1=pass):",
              "", json.dumps(analysis["reranking_paired_proxy_transitions"], sort_keys=True),
              "", "## Next experiments (proposals, not claimed improvements)", "",
              "1. Audit missing citations/refusals against evidence and draft reference labels.",
              "2. Audit extra citations: a draft filename set may omit a valid supporting source.",
              "3. Keep the historical reranker result; do not assume reranking helps quality.",
              "4. Only tune after human review; preserve holdout for a separately approved run.",
              "", "Open review.html, grade both dimensions and export labels. Draft reference "
              "answers are aids, not gold. Do not mark AI-generated opinions as human labels.",
              "", "Raw answers, evidence, source contents and reviewer notes stay local."]
    (output / "diagnosis.md").write_text("\n".join(lines) + "\n")
    return {"review_rows": len(packet["rows"]), "paired_cases": analysis["paired_completed_cases"],
            "api_calls": 0, "human_reviews_created": 0}


def import_labels(directory, packet_dir, labels_path, output):
    reports, cases = load_suite(directory)
    packet, mapping = build_packet(reports, cases)
    manifest = json.loads((packet_dir / "manifest.json").read_text())
    if manifest != {"fingerprint": packet["fingerprint"], "mapping": mapping}:
        raise ValueError("Review packet no longer matches the exact responses/configuration")
    groups = {name: [] for name in VARIANTS}
    seen = set()
    for line in labels_path.read_text().splitlines():
        if not line.strip():
            continue
        label = json.loads(line)
        key = label.get("review_key")
        if key not in mapping or key in seen:
            raise ValueError("Unknown or duplicate review key")
        seen.add(key)
        item = mapping[key]
        if (label.get("response_hash") != item["response_hash"]
                or type(label.get("correct")) is not bool
                or type(label.get("supported")) is not bool
                or label.get("human_attested") is not True
                or not isinstance(label.get("reviewer"), str) or not label["reviewer"].strip()
                or label.get("failure_stage") not in STAGES
                or not isinstance(label.get("notes"), str)):
            raise ValueError("Require exact hash, two boolean grades and attested human reviewer")
        if (not label["correct"] or not label["supported"]
                or label["failure_stage"] == "reference_label") and not label["notes"].strip():
            raise ValueError("Failed grades/reference-label disputes require explanatory notes")
        groups[item["variant"]].append({**label, "id": item["id"]})
    if not seen:
        raise ValueError("No completed human reviews to import")
    output.mkdir(parents=True, exist_ok=False)
    summaries = {}
    for variant, labels in groups.items():
        target = output / f"{variant}.jsonl"
        target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labels))
        summaries[variant] = apply_reviews(reports[variant], target)
    paired = set.intersection(*({row["id"] for row in labels} for labels in groups.values()))
    comparison = {"api_calls": 0, "paired_reviewed_cases": len(paired),
                  "warning": "Selected development review, not held-out accuracy. "
                             "Compare only identical reviewed case IDs across engines.",
                  "variants": {name: {"reviewed": values["reviewed"],
                                      "eligible": values["eligible"],
                                      "coverage": values["coverage"],
                                      "paired_correctness": mean(r["correct"] for r in groups[name]
                                                                 if r["id"] in paired)
                                      if paired else None,
                                      "paired_faithfulness": mean(
                                          r["supported"] for r in groups[name] if r["id"] in paired)
                                      if paired else None} for name, values in summaries.items()}}
    (output / "human-summary.json").write_text(json.dumps(comparison, indent=2))
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=PROJECT_ROOT / "evals/reports/public-benchmark")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--packet", type=Path)
    args = parser.parse_args()
    # Force local artifacts into the ignored report tree, never a public/static asset folder.
    reports_root = (PROJECT_ROOT / "evals/reports").resolve()
    if not args.output.resolve().is_relative_to(reports_root):
        parser.error("Output must stay inside ignored evals/reports")
    if args.labels:
        if not args.packet:
            parser.error("--packet is required with --labels")
        result = import_labels(args.directory, args.packet, args.labels, args.output)
    else:
        result = prepare(args.directory, args.output)
    print(json.dumps(result))


REVIEW_HTML = (Path(__file__).with_name("assets") / "quality_review.html").read_text()


if __name__ == "__main__":
    main()

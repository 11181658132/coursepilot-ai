"""Offline, explicitly denominated human-review metrics and local repair queue.

Never creates labels. Automated proxy grades are compared with, not substituted
for, attested human judgments. Raw notes belong only in ignored local reports.
"""

from collections import Counter

from app.benchmark import VARIANTS

NEXT_ACTION = {
    "retrieval": "Inspect missing evidence, filters and chunk boundaries before generator tuning.",
    "answer": "Check interpretation, omissions and refusal behavior against sufficient evidence.",
    "citation": "Check claim-to-source support; verbatim quote presence alone is insufficient.",
    "reference_label": "Resolve draft-reference dispute separately; preserve historical labels.",
    "upstream": "Inspect availability; do not count blocked calls as quality failures.",
    "uncertain": "Request a second human review before selecting a fix.",
    "none": "No repair proposed by this reviewer.",
}


def metric(values):
    values = list(values)
    return {
        "numerator": sum(values),
        "denominator": len(values),
        "rate": sum(values) / len(values) if values else None,
    }


def quality(labels):
    return {
        "correctness": metric(r["correct"] for r in labels),
        "faithfulness": metric(r["supported"] for r in labels),
        "both": metric(r["correct"] and r["supported"] for r in labels),
    }


def analyse_reviews(reports, cases, groups):
    paired = set.intersection(*({r["id"] for r in groups[name]} for name in VARIANTS))
    variants, queue = {}, []
    total_eligible = total_reviewed = 0
    for name in VARIANTS:
        rows = reports[name]["results"]
        eligible = {r["id"]: r for r in rows if not r.get("error")}
        labels = groups[name]
        total_eligible += len(eligible)
        total_reviewed += len(labels)
        confusion = {
            key: 0
            for key in (
                "proxy_pass_human_pass",
                "proxy_pass_human_fail",
                "proxy_fail_human_pass",
                "proxy_fail_human_fail",
            )
        }
        for label in labels:
            proxy = "pass" if eligible[label["id"]]["passed"] else "fail"
            human = "pass" if label["correct"] and label["supported"] else "fail"
            confusion[f"proxy_{proxy}_human_{human}"] += 1
            if human == "fail" or label["failure_stage"] == "reference_label":
                queue.append(
                    {
                        "variant": name,
                        "case_id": label["id"],
                        "response_hash": label["response_hash"],
                        "category": cases[label["id"]].category,
                        "failure_stage": label["failure_stage"],
                        "correct": label["correct"],
                        "supported": label["supported"],
                        "notes": label["notes"],
                        "suggested_investigation": NEXT_ACTION[label["failure_stage"]],
                    }
                )
        variants[name] = {
            "total_outputs": len(rows),
            "eligible_outputs": len(eligible),
            "blocked_outputs": len(rows) - len(eligible),
            "reviewed_outputs": len(labels),
            "review_coverage": len(labels) / len(eligible) if eligible else 0,
            "reviewed_subset": quality(labels),
            "paired_subset": quality([r for r in labels if r["id"] in paired]),
            "categories": {
                category: {
                    "eligible_outputs": sum(cases[key].category == category for key in eligible),
                    **quality([r for r in labels if cases[r["id"]].category == category]),
                }
                for category in sorted({cases[key].category for key in eligible})
            },
            "human_primary_stages": dict(Counter(r["failure_stage"] for r in labels)),
            "reference_disputes": sum(r["failure_stage"] == "reference_label" for r in labels),
            "proxy_vs_human_both": confusion,
        }
    return {
        "mode": "offline-human-development-analysis",
        "api_calls": 0,
        "fingerprint": reports[VARIANTS[0]]["fingerprint"],
        "status": (
            "awaiting_human_reviews"
            if not total_reviewed
            else "complete_development_review"
            if total_reviewed == total_eligible
            else "partial"
        ),
        "eligible_outputs": total_eligible,
        "reviewed_outputs": total_reviewed,
        "paired_reviewed_cases": len(paired),
        "variants": variants,
        "repair_queue": queue,
        "limitations": [
            "Historical cached responses, not a re-evaluation of the current deployed code.",
            "Selected development sample; not held-out or production accuracy.",
            "Reference drafts and reviewer diagnoses may be wrong; resolve disputes separately.",
            "Rates are null when no human grades exist; compare engines on the paired subset only.",
            "Proxy agreement is descriptive, not independent grader validation or causal proof.",
        ],
    }


def render_analysis(analysis):
    lines = [
        "# Human RAG development review",
        "",
        f"Status: {analysis['status']}. API calls: 0.",
        "",
        f"Human-reviewed outputs: {analysis['reviewed_outputs']} / "
        f"{analysis['eligible_outputs']}; paired reviewed questions: "
        f"{analysis['paired_reviewed_cases']}.",
        "",
        "| Engine | Reviewed / eligible | Correct | Supported | Both | Paired both |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    def display(value):
        if not value["denominator"]:
            return "— (0 reviewed)"
        return f"{value['numerator']}/{value['denominator']} ({value['rate']:.1%})"

    for name, values in analysis["variants"].items():
        subset = values["reviewed_subset"]
        scores = [display(subset[key]) for key in ("correctness", "faithfulness", "both")]
        scores.append(display(values["paired_subset"]["both"]))
        lines.append(
            f"| {name} | {values['reviewed_outputs']}/{values['eligible_outputs']} | "
            + " | ".join(scores)
            + " |"
        )
    lines += [
        "",
        "## Failure investigation queue",
        "",
        "Counts are reviewer-selected primary stages, not automatic root-cause proof.",
        "",
    ]
    counts = Counter(row["failure_stage"] for row in analysis["repair_queue"])
    for stage, count in sorted(counts.items()):
        lines.append(f"- {stage}: {count}. {NEXT_ACTION[stage]}")
    if not counts:
        lines.append("No reviewed repair items yet; this does not establish absence of failures.")
    lines += [
        "",
        "Detailed case IDs, response hashes and notes stay in local review-analysis.json.",
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in analysis["limitations"]],
    ]
    return "\n".join(lines) + "\n"

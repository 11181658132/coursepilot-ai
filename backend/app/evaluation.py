import hashlib
import html
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional

from app.models import ChatResponse
from app.services.openai_kb import OpenAIKnowledgeBase
from app.services.state import save_state

SCORING_VERSION = 3


@dataclass
class EvalCase:
    id: str
    course: str
    material_type: str
    question: str
    expected_files: List[str]
    expected_keywords: List[Any]
    should_refuse: bool = False
    split: str = "legacy"
    group: str = "legacy"
    category: str = "concept"
    language: str = "unspecified"
    reference_answer: str = ""
    source_quotes: List[dict] = field(default_factory=list)
    forbidden_keywords: List[str] = field(default_factory=list)
    require_all_sources: bool = False
    label_status: str = "unreviewed"


@dataclass
class EvalResult:
    id: str
    course: str
    material_type: str
    question: str
    passed: bool
    answer: str
    cited_files: List[str]
    retrieved_files: List[str]
    expected_file_hit: Optional[bool]
    citation_precision: Optional[float]
    keyword_recall: Optional[float]
    refusal_detected: bool
    latency_ms: int
    estimated_cost_usd: float
    error: Optional[str] = None
    split: str = "legacy"
    category: str = "concept"
    retrieval_recall: Optional[float] = None
    reciprocal_rank: Optional[float] = None
    quote_validity: Optional[float] = None
    failure_categories: List[str] = field(default_factory=list)
    evidence: List[dict] = field(default_factory=list)
    citations: List[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    reference_answer: str = ""


REFUSAL_MARKERS = (
    "资料中没有",
    "资料没有",
    "无法从",
    "没有足够",
    "未提供",
    "not enough information",
    "do not contain",
    "cannot determine",
    "can't determine",
    "not enough evidence", "insufficient evidence", "not provided", "does not specify",
    "does not contain", "cannot find", "不足以", "未包含", "没有提供", "不包含",
    "cannot provide", "can't provide", "cannot disclose", "can't disclose",
    "cannot comply", "can't comply", "无法提供", "不能泄露", "无法执行", "不能执行",
)


def load_cases(path: Path) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            case = EvalCase(**payload)
            if not all(isinstance(value, str) and value.strip()
                       for value in (case.id, case.course, case.question)):
                raise ValueError("Case ID, course, question must be nonempty strings")
            if case.material_type not in {"all", "lecture", "tutorial", "other"}:
                raise ValueError("Invalid material type")
            if type(case.should_refuse) is not bool or type(case.require_all_sources) is not bool:
                raise ValueError("Refusal/source flags must be booleans")
            if not isinstance(case.expected_files, list) or not all(
                    isinstance(item, str) and item for item in case.expected_files):
                raise ValueError("Expected files must be a list of filenames")
            if not isinstance(case.expected_keywords, list):
                raise ValueError("Expected keywords must be a list")
            for group in case.expected_keywords:
                alternatives = [group] if isinstance(group, str) else group
                if not isinstance(alternatives, list) or not alternatives or not all(
                        isinstance(word, str) and word.strip() for word in alternatives):
                    raise ValueError("Keyword alternatives must be nonempty strings")
            if any(old.id == case.id for old in cases):
                raise ValueError("Duplicate evaluation ID")
            cases.append(case)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid eval case at {path}:{line_number}: {exc}") from exc
    if not cases:
        raise ValueError(f"No evaluation cases found in {path}")
    return cases


def _normalise(value: str) -> str:
    return "".join(value.lower().split())


def score_case(case: EvalCase, response: ChatResponse) -> EvalResult:
    cited_files = list(dict.fromkeys(item.filename for item in response.citations))
    retrieved_files = list(dict.fromkeys(item.filename for item in response.evidence))
    expected = set(case.expected_files)
    # v2: citations alone never count as a retrieval hit.
    expected_file_hit = bool(expected & set(retrieved_files)) if expected else None
    retrieval_recall = len(expected & set(retrieved_files)) / len(expected) if expected else None
    reciprocal_rank = next((1 / rank for rank, name in enumerate(retrieved_files, 1)
                            if name in expected), 0.0) if expected else None
    citation_precision = (
        len(expected & set(cited_files)) / len(cited_files) if cited_files else 0.0
    ) if expected else None
    quoted = [item for item in response.citations if item.quote]

    def quote_matches(item, evidence):
        quote = " ".join(item.quote.split())
        same_chunk = not item.chunk_id or item.chunk_id == evidence.chunk_id
        same_source = not item.source_id or item.source_id == evidence.source_id
        return (len(quote) >= 8 and same_chunk and same_source
                and item.filename == evidence.filename
                and quote in " ".join(evidence.text.split()))

    quote_validity = (
        sum(any(quote_matches(item, evidence) for evidence in response.evidence)
            for item in quoted) / len(quoted)
        if quoted else None
    )

    normalised_answer = _normalise(response.answer)
    keyword_groups = [
        [item] if isinstance(item, str) else item for item in case.expected_keywords
    ]
    keyword_hits = sum(
        1
        for alternatives in keyword_groups
        if any(_normalise(keyword) in normalised_answer for keyword in alternatives)
    )
    keyword_recall = (
        keyword_hits / len(keyword_groups) if keyword_groups else None
    )
    refusal_detected = any(marker in response.answer.lower() for marker in REFUSAL_MARKERS)

    failures = []
    if any(_normalise(word) in normalised_answer for word in case.forbidden_keywords):
        failures.append("forbidden_output")
    if case.should_refuse:
        if not refusal_detected:
            failures.append("refusal_not_detected")
    else:
        if expected_file_hit is False:
            failures.append("retrieval_miss")
        if case.require_all_sources and retrieval_recall != 1:
            failures.append("retrieval_incomplete")
        if not cited_files:
            failures.append("citation_missing")
        elif expected and citation_precision < 1:
            failures.append("citation_irrelevant")
        if set(cited_files) - set(retrieved_files):
            failures.append("citation_not_retrieved")
        if case.require_all_sources and not expected.issubset(cited_files):
            failures.append("citation_incomplete")
        if keyword_recall is not None and keyword_recall < 0.5:
            failures.append("incomplete_answer_proxy")
        if refusal_detected and not cited_files:
            failures.append("unexpected_refusal_proxy")
    if quote_validity is not None and quote_validity < 1:
        failures.append("invalid_quote")
    passed = not failures

    return EvalResult(
        id=case.id,
        course=case.course,
        material_type=case.material_type,
        question=case.question,
        passed=passed,
        answer=response.answer,
        cited_files=cited_files,
        retrieved_files=retrieved_files,
        expected_file_hit=expected_file_hit,
        citation_precision=citation_precision,
        keyword_recall=keyword_recall,
        refusal_detected=refusal_detected,
        latency_ms=response.latency_ms,
        estimated_cost_usd=response.estimated_cost_usd,
        split=case.split, category=case.category, retrieval_recall=retrieval_recall,
        reciprocal_rank=reciprocal_rank, quote_validity=quote_validity,
        failure_categories=failures, usage=response.usage,
        evidence=[item.model_dump() for item in response.evidence],
        citations=[item.model_dump() for item in response.citations],
        reference_answer=case.reference_answer,
    )


def summarise(results: Iterable[EvalResult]) -> Dict[str, Any]:
    items = list(results)
    successful = [item for item in items if not item.error]
    blocked = [item for item in items if item.error]

    def average(values: Iterable[Optional[float]]) -> Optional[float]:
        present = [value for value in values if value is not None]
        return round(mean(present), 4) if present else None

    latencies = sorted(item.latency_ms for item in successful)

    def percentile(fraction):
        return latencies[max(0, math.ceil(len(latencies) * fraction) - 1)] if latencies else None

    return {
        "total": len(items),
        "completed": len(successful),
        "blocked": len(blocked),
        "passed": sum(item.passed for item in successful),
        "failed": sum(not item.passed for item in successful),
        "pass_rate": round(sum(item.passed for item in successful) / len(successful), 4)
        if successful
        else 0,
        "retrieval_hit_rate": average(
            1.0 if item.expected_file_hit else 0.0
            for item in successful
            if item.expected_file_hit is not None
        ),
        "citation_precision": average(item.citation_precision for item in successful),
        "keyword_recall": average(item.keyword_recall for item in successful),
        "retrieval_recall": average(item.retrieval_recall for item in successful),
        "mrr": average(item.reciprocal_rank for item in successful),
        "quote_validity": average(item.quote_validity for item in successful),
        "coverage": round(len(successful) / len(items), 4) if items else 0,
        "overall_pass_rate": round(sum(item.passed for item in successful) / len(items), 4)
        if items else 0,
        "p50_latency_ms": percentile(0.50), "p95_latency_ms": percentile(0.95),
        "input_tokens": sum(item.usage.get("input_tokens", 0) for item in successful),
        "output_tokens": sum(item.usage.get("output_tokens", 0) for item in successful),
        "embedding_tokens": sum(item.usage.get("embedding_tokens", 0) for item in successful),
        "failure_categories": dict(Counter(reason for item in items
                                           for reason in item.failure_categories)),
        "average_latency_ms": round(mean(item.latency_ms for item in successful))
        if successful
        else 0,
        "estimated_cost_usd": round(
            sum(item.estimated_cost_usd for item in successful), 6
        ),
    }


def run_evaluation(
    knowledge_base: OpenAIKnowledgeBase, cases: Iterable[EvalCase]
) -> Dict[str, Any]:
    results: List[EvalResult] = []
    for case in cases:
        try:
            response = knowledge_base.chat(
                course=case.course,
                material_type=case.material_type,
                question=case.question,
            )
            results.append(score_case(case, response))
        except Exception as exc:
            results.append(
                EvalResult(
                    id=case.id,
                    course=case.course,
                    material_type=case.material_type,
                    question=case.question,
                    passed=False,
                    answer="",
                    cited_files=[],
                    retrieved_files=[],
                    expected_file_hit=None,
                    citation_precision=None,
                    keyword_recall=None,
                    refusal_detected=False,
                    latency_ms=0,
                    estimated_cost_usd=0,
                    error=type(exc).__name__, split=case.split, category=case.category,
                    failure_categories=["upstream_error"],
                )
            )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scoring_version": SCORING_VERSION,
        "summary": summarise(results),
        "results": [asdict(result) for result in results],
    }


def resume_evaluation(
    knowledge_base: OpenAIKnowledgeBase,
    cases: Iterable[EvalCase],
    previous_report: Dict[str, Any],
) -> Dict[str, Any]:
    cases_list = list(cases)
    if previous_report.get("scoring_version") != SCORING_VERSION:
        raise ValueError("Cannot resume a report scored with a different metric version")
    previous = {item["id"]: item for item in previous_report.get("results", [])}
    if any(case.id in previous and previous[case.id]["question"] != case.question
           for case in cases_list):
        raise ValueError("Cannot resume after changing a case question")
    pending = [
        case
        for case in cases_list
        if case.id not in previous or previous[case.id].get("error")
    ]
    fresh = run_evaluation(knowledge_base, pending)
    fresh_by_id = {item["id"]: item for item in fresh["results"]}
    merged = [fresh_by_id.get(case.id) or previous.get(case.id) for case in cases_list]
    merged = [item for item in merged if item]
    results = [EvalResult(**item) for item in merged]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scoring_version": SCORING_VERSION,
        "summary": summarise(results),
        "results": [asdict(result) for result in results],
    }


def write_report(report: Dict[str, Any], output_directory: Path) -> Dict[str, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "latest.json"
    html_path = output_directory / "latest.html"
    save_state(json_path, report)

    summary = report["summary"]
    rows = []
    for result in report["results"]:
        evidence_json = html.escape(json.dumps(result.get("evidence", []),
                                               ensure_ascii=False, indent=2))
        if result["error"]:
            status, status_class = "BLOCKED", "blocked"
        else:
            status = "PASS" if result["passed"] else "FAIL"
            status_class = "pass" if result["passed"] else "fail"
        rows.append(
            "<tr>"
            f'<td><span class="badge {status_class}">{status}</span></td>'
            f"<td>{html.escape(result['id'])}</td>"
            f"<td>{html.escape(result['course'])}/{html.escape(result['material_type'])}</td>"
            f"<td>{html.escape(result['question'])}</td>"
            f"<td>{html.escape(', '.join(result['cited_files']) or '—')}</td>"
            f"<td>{html.escape(', '.join(result.get('failure_categories', [])) or '—')}"
            f"<details><summary>Inspect answer and evidence</summary>"
            f"<pre>{html.escape(result['answer'])}</pre>"
            f"<pre>Reference: {html.escape(result.get('reference_answer', ''))}</pre>"
            f"<pre>{evidence_json}"
            "</pre></details></td>"
            f"<td>{result['latency_ms']:,} ms</td>"
            f"<td>${result['estimated_cost_usd']:.6f}</td>"
            "</tr>"
        )
    cards = [
        ("Proxy pass rate", f"{summary['pass_rate']:.0%}"),
        ("Completed", f"{summary['completed']}/{summary['total']}"),
        ("Blocked", str(summary["blocked"])),
        ("Retrieval hit", _percentage(summary["retrieval_hit_rate"])),
        ("Citation precision", _percentage(summary["citation_precision"])),
        ("Keyword recall", _percentage(summary["keyword_recall"])),
        ("Average latency", f"{summary['average_latency_ms']:,} ms"),
        ("P50 latency", str(summary.get("p50_latency_ms", "—")) + " ms"),
        ("P95 latency", str(summary.get("p95_latency_ms", "—")) + " ms"),
        ("Estimated cost", f"${summary['estimated_cost_usd']:.6f}"),
    ]
    card_markup = "".join(
        f'<article class="card"><span>{html.escape(label)}</span><strong>{value}</strong></article>'
        for label, value in cards
    )
    reviewed_count = report.get("human_review", {}).get("reviewed", 0)
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CoursePilot AI Evaluation</title>
<style>
:root{{--ink:#172234;--muted:#677489;--surface:#fff;--line:#dfe5ec;--green:#087f5b;--red:#c92a2a}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f5f7fa;color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1180px;margin:48px auto;padding:0 24px}}
h1{{font-size:34px;margin:0 0 6px}}.meta{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}}
.grid{{margin:28px 0}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:14px}}
.card{{padding:18px;box-shadow:0 6px 20px #2230440a}}
.card span{{display:block;color:var(--muted);font-size:13px}}
.card strong{{display:block;font-size:25px;margin-top:5px}}
.table-wrap{{overflow:auto;background:var(--surface);border:1px solid var(--line)}}
.table-wrap{{border-radius:14px}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:13px 14px;text-align:left;border-bottom:1px solid var(--line)}}
th,td{{vertical-align:top}}
th{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
tr:last-child td{{border:0}}
pre{{white-space:pre-wrap;word-break:break-word;max-width:650px;max-height:360px;overflow:auto}}
.badge{{font-weight:700;font-size:12px}}.pass{{color:var(--green)}}.fail{{color:var(--red)}}
.blocked{{color:#b26a00}}
</style></head><body><main><h1>CoursePilot AI Evaluation</h1>
<p class="meta">Generated {html.escape(report['generated_at'])} · {summary['total']} test cases</p>
<p>Automated metrics are proxies, not answer accuracy or semantic faithfulness.
Human-review coverage: {html.escape(str(reviewed_count))} cases.</p>
<section class="grid">{card_markup}</section><div class="table-wrap"><table><thead><tr>
<th>Status</th><th>ID</th><th>Scope</th><th>Question</th><th>Citations</th><th>Diagnosis</th><th>Latency</th><th>Cost</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div></main></body></html>"""
    html_path.write_text(document, encoding="utf-8")
    return {"json": json_path, "html": html_path}


def _percentage(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0%}"


def result_hash(report: dict, result: dict) -> str:
    payload = {"fingerprint": report.get("fingerprint"), "id": result["id"],
               "question": result["question"], "answer": result["answer"],
               "citations": result.get("citations", []), "evidence": result.get("evidence", [])}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_review_template(report: dict, path: Path):
    if path.exists():
        raise FileExistsError("Refusing to overwrite human review labels")
    rows = [{"id": result["id"], "response_hash": result_hash(report, result),
             "correct": None, "supported": None, "reviewer": "", "notes": ""}
            for result in report["results"] if not result["error"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def apply_reviews(report: dict, path: Path) -> dict:
    results = {item["id"]: item for item in report["results"] if not item["error"]}
    reviewed = []
    seen = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in seen or row["id"] not in results:
            raise ValueError("Duplicate or unknown human review case")
        seen.add(row["id"])
        if row["response_hash"] != result_hash(report, results[row["id"]]):
            raise ValueError("Human labels refer to a different response/configuration")
        if row["correct"] is None and row["supported"] is None:
            continue
        if (type(row["correct"]) is not bool or type(row["supported"]) is not bool
                or not isinstance(row["reviewer"], str) or not row["reviewer"].strip()):
            raise ValueError("Both boolean grades and a reviewer are required")
        reviewed.append(row)
    return {"reviewed": len(reviewed), "eligible": len(results),
            "coverage": len(reviewed) / len(results) if results else 0,
            "correctness": mean(row["correct"] for row in reviewed) if reviewed else None,
            "faithfulness": mean(row["supported"] for row in reviewed) if reviewed else None,
            "labels": reviewed}

import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional

from app.models import ChatResponse
from app.services.openai_kb import OpenAIKnowledgeBase


@dataclass
class EvalCase:
    id: str
    course: str
    material_type: str
    question: str
    expected_files: List[str]
    expected_keywords: List[Any]
    should_refuse: bool = False


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
)


def load_cases(path: Path) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            cases.append(
                EvalCase(
                    id=payload["id"],
                    course=payload["course"],
                    material_type=payload.get("material_type", "all"),
                    question=payload["question"],
                    expected_files=payload.get("expected_files", []),
                    expected_keywords=payload.get("expected_keywords", []),
                    should_refuse=payload.get("should_refuse", False),
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid eval case at {path}:{line_number}: {exc}") from exc
    if not cases:
        raise ValueError(f"No evaluation cases found in {path}")
    return cases


def _normalise(value: str) -> str:
    return "".join(value.lower().split())


def score_case(case: EvalCase, response: ChatResponse) -> EvalResult:
    cited_files = list(dict.fromkeys(item.filename for item in response.citations))
    retrieved_files = list(dict.fromkeys(item.filename for item in response.evidence))
    available_files = set(cited_files + retrieved_files)
    expected = set(case.expected_files)
    expected_file_hit = bool(expected & available_files) if expected else None
    citation_precision = (
        len(expected & set(cited_files)) / len(cited_files) if expected and cited_files else None
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

    if case.should_refuse:
        passed = refusal_detected
    else:
        file_ok = expected_file_hit is not False
        keyword_ok = keyword_recall is None or keyword_recall >= 0.5
        passed = file_ok and keyword_ok and bool(cited_files)

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
    )


def summarise(results: Iterable[EvalResult]) -> Dict[str, Any]:
    items = list(results)
    successful = [item for item in items if not item.error]
    blocked = [item for item in items if item.error]

    def average(values: Iterable[Optional[float]]) -> Optional[float]:
        present = [value for value in values if value is not None]
        return round(mean(present), 4) if present else None

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
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarise(results),
        "results": [asdict(result) for result in results],
    }


def resume_evaluation(
    knowledge_base: OpenAIKnowledgeBase,
    cases: Iterable[EvalCase],
    previous_report: Dict[str, Any],
) -> Dict[str, Any]:
    cases_list = list(cases)
    previous = {item["id"]: item for item in previous_report.get("results", [])}
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
        "summary": summarise(results),
        "results": [asdict(result) for result in results],
    }


def write_report(report: Dict[str, Any], output_directory: Path) -> Dict[str, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "latest.json"
    html_path = output_directory / "latest.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report["summary"]
    rows = []
    for result in report["results"]:
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
            f"<td>{result['latency_ms']:,} ms</td>"
            f"<td>${result['estimated_cost_usd']:.6f}</td>"
            "</tr>"
        )
    cards = [
        ("Pass rate", f"{summary['pass_rate']:.0%}"),
        ("Completed", f"{summary['completed']}/{summary['total']}"),
        ("Blocked", str(summary["blocked"])),
        ("Retrieval hit", _percentage(summary["retrieval_hit_rate"])),
        ("Citation precision", _percentage(summary["citation_precision"])),
        ("Keyword recall", _percentage(summary["keyword_recall"])),
        ("Average latency", f"{summary['average_latency_ms']:,} ms"),
        ("Estimated cost", f"${summary['estimated_cost_usd']:.6f}"),
    ]
    card_markup = "".join(
        f'<article class="card"><span>{html.escape(label)}</span><strong>{value}</strong></article>'
        for label, value in cards
    )
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
.badge{{font-weight:700;font-size:12px}}.pass{{color:var(--green)}}.fail{{color:var(--red)}}
.blocked{{color:#b26a00}}
</style></head><body><main><h1>CoursePilot AI Evaluation</h1>
<p class="meta">Generated {html.escape(report['generated_at'])} · {summary['total']} test cases</p>
<section class="grid">{card_markup}</section><div class="table-wrap"><table><thead><tr>
<th>Status</th><th>ID</th><th>Scope</th><th>Question</th><th>Citations</th><th>Latency</th><th>Cost</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div></main></body></html>"""
    html_path.write_text(document, encoding="utf-8")
    return {"json": json_path, "html": html_path}


def _percentage(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0%}"

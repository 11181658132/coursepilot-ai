"""Deterministic post-execution gates, not model/semantic correctness grading."""

from datetime import datetime, timezone


def _citations_current(citations, course, lookup):
    if not citations:
        return False
    for citation in citations:
        identifier, quote = citation.get("chunk_id"), citation.get("quote", "")
        if not identifier or not isinstance(quote, str) or len(quote.strip()) < 8:
            return False
        try:
            source = lookup(identifier)
        except Exception:
            return False  # Do not leak filesystem/provider errors or fail open.
        if not isinstance(source, dict):
            return False
        path = source.get("path", "")
        if (source.get("course", path.split("/")[0]) != course
                or (citation.get("path") and citation["path"] != path)
                or " ".join(quote.split()) not in " ".join(source.get("text", "").split())
                or (citation.get("document_version") and
                    citation["document_version"] != source.get("document_version"))):
            return False
    return True


def _report(checks):
    return {"version": 1, "status": "passed" if all(checks.values()) else "blocked",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{"id": key, "status": "passed" if passed else "blocked"}
                       for key, passed in checks.items()],
            "goal_coverage": "requires_human_review",
            "semantic_support": "requires_human_review",
            "scope": "structure, state invariants and current verbatim source presence only"}


def check_plan(state, lookup):
    tasks, request = state.get("tasks", []), state.get("request", {})
    days = request.get("days", 0)
    structure = bool(tasks) and sorted(t.get("day", 0) for t in tasks) == list(range(1, days + 1))
    structure = structure and len({t.get("id") for t in tasks}) == len(tasks)
    structure = structure and all(
        t.get("id") and t.get("done") is False and t.get("minutes") == request.get("daily_minutes")
        and all(isinstance(t.get(key), str) and t[key].strip()
                for key in ("title", "study_goal", "quiz_question", "quiz_answer")) for t in tasks)
    return _report({"task_constraints": bool(structure), "current_evidence": bool(tasks) and all(
        _citations_current(t.get("citations", []), request.get("course"), lookup) for t in tasks)})


def check_feedback(parent, feedback, lookup):
    source = next((t for t in parent["tasks"] if t["id"] == feedback["task_id"]), None)
    eligible = {t["id"]: t for t in parent["tasks"]
                if source and not t["done"] and t["day"] > source["day"]}
    assessment, edits = feedback.get("assessment", {}), feedback.get("adjustments", [])
    verdict = assessment.get("verdict")
    constraints = (bool(source) and verdict in {"mostly_correct", "needs_review", "uncertain"}
                   and (verdict != "needs_review" or bool(assessment.get("weaknesses")))
                   and (verdict == "needs_review" or not edits)
                   and (verdict == "needs_review" or not assessment.get("weaknesses")))
    seen = set()
    for edit in edits:
        if (set(edit) != {"task_id", "title", "study_goal", "quiz_question", "quiz_answer",
                         "citations"} or edit.get("task_id") not in eligible
                or edit["task_id"] in seen):
            constraints = False
        seen.add(edit.get("task_id"))
    citations = assessment.get("citations", [])
    grounded = (verdict == "uncertain" and not citations and not edits) or (
        _citations_current(citations, parent["course"], lookup) and all(
            _citations_current(edit.get("citations", []), parent["course"], lookup)
            for edit in edits))
    return _report({"plan_version": parent["version"] == feedback["base_version"],
                    "progress_and_edit_constraints": bool(constraints),
                    "current_evidence": bool(grounded)})


def require_passed(report):
    checks = report.get("checks", []) if isinstance(report, dict) else []
    if (not isinstance(report, dict) or report.get("version") != 1
            or report.get("status") != "passed" or not checks
            or any(item.get("status") != "passed" for item in checks)):
        from app.services.study_store import StudyConflict

        allowed = {"task_constraints", "current_evidence", "plan_version",
                   "progress_and_edit_constraints"}
        failed = ", ".join(item["id"] for item in checks
                           if item.get("status") != "passed" and item.get("id") in allowed)
        raise StudyConflict("执行后校验未通过（" + failed +
                            "）；未启用计划或应用调整，请核对来源和进度。")

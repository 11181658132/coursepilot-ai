import copy
import unittest

from app.services.agent_checks import check_feedback, check_plan, require_passed
from app.services.study_store import StudyConflict


class PostcheckTests(unittest.TestCase):
    def setUp(self):
        self.quote = "Evidence must support the claims being made."
        self.citation = {"chunk_id": "one", "path": "DEMO101/lecture/source.md",
                         "quote": self.quote, "document_version": "a" * 64}
        self.source = {**self.citation, "text": self.quote, "course": "DEMO101"}
        self.tasks = [{"id": str(day), "day": day, "minutes": 30, "done": False,
                       "title": "Review", "study_goal": "Read evidence", "quiz_question": "Why?",
                       "quiz_answer": "To check claims", "citations": [self.citation]}
                      for day in (1, 2)]
        self.plan = {"course": "DEMO101", "version": 3, "tasks": self.tasks,
                     "request": {"course": "DEMO101", "days": 2, "daily_minutes": 30}}

    def test_plan_rejects_wrong_days_progress_minutes_and_missing_citations(self):
        for field, value in (("done", True), ("day", 3), ("minutes", 90), ("citations", [])):
            state = copy.deepcopy(self.plan)
            state["tasks"][0][field] = value
            self.assertEqual(check_plan(state, lambda _: self.source)["status"], "blocked")

    def test_real_quote_in_foreign_or_changed_source_is_not_accepted(self):
        for field, value in (("course", "OTHER"), ("document_version", "b" * 64),
                             ("path", "OTHER/secret.md"), ("text", "Unrelated text")):
            source = {**self.source, field: value}
            self.assertEqual(check_plan(self.plan, lambda _, source=source: source)["status"],
                             "blocked")

    def test_source_lookup_errors_fail_closed_without_returning_exception_details(self):
        def fail(_):
            raise OSError("private filesystem path")
        report = check_plan(self.plan, fail)
        self.assertEqual(report["status"], "blocked")
        self.assertNotIn("private", str(report))

    def test_uncertain_feedback_cannot_edit_completed_tasks_or_add_weaknesses(self):
        feedback = {"base_version": 3, "task_id": "1", "assessment": {
            "verdict": "uncertain", "citations": [], "weaknesses": []}, "adjustments": []}
        self.assertEqual(check_feedback(self.plan, feedback, lambda _: None)["status"], "passed")
        feedback["assessment"]["weaknesses"] = ["Unverified weakness"]
        self.assertEqual(check_feedback(self.plan, feedback, lambda _: None)["status"], "blocked")

    def test_confirmation_does_not_trust_a_pass_flag_with_failed_or_unknown_checks(self):
        for report in ({"status": "passed"}, {"version": 99, "status": "passed", "checks": []},
                       {"version": 1, "status": "passed", "checks": [
                           {"id": "current_evidence", "status": "blocked"}]}):
            with self.assertRaises(StudyConflict):
                require_passed(report)

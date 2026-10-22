import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.config import Settings, get_settings
from app.main import app, get_feedback_agent, get_hybrid_base, get_study_store
from app.services.agent_checks import check_feedback
from app.services.study_agent import PlanRequest
from app.services.study_feedback import FeedbackAgent, FeedbackRequest
from app.services.study_store import StudyBudgetExceeded, StudyConflict, StudyStore
from fastapi.testclient import TestClient

QUOTE = "Retrieved evidence must support the answer, not just share keywords."


def response(name, arguments):
    return SimpleNamespace(status="completed",
        usage=SimpleNamespace(input_tokens=100, output_tokens=50), output=[SimpleNamespace(
            type="function_call", name=name, call_id="test", arguments=json.dumps(arguments))])


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StudyStore(Path(self.temp.name) / "plans.sqlite3")
        self.config = Settings(_env_file=None, openai_api_key="test-only")
        request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", goal="Review RAG", days=3)
        run, _ = self.store.start(request)
        tasks = [{"id": uuid.uuid4().hex, "day": day, "title": f"Day {day}", "minutes": 45,
                  "study_goal": "Review evidence", "quiz_question": "What should support claims?",
                  "quiz_answer": "Retrieved course evidence.", "done": False, "citations": []}
                 for day in (1, 2, 3)]
        self.store.finish(run["id"], "draft", {"request": request.model_dump(mode="json"),
                                               "tasks": tasks, "trace": []})
        self.plan = self.store.confirm(run["id"], 1)
        self.task = tasks[0]
        self.request = FeedbackRequest(request_id=uuid.uuid4(), version=self.plan["version"],
                                       answer="Any keyword overlap proves an answer correct.")
        self.search = Mock(return_value=[{"filename": "source.md", "chunk_id": "test",
            "path": "DEMO101/lecture/source.md", "text": QUOTE, "score": 1.0}])
        self.base = SimpleNamespace(retrieve=self.search, source=lambda identifier: next(
            (row for row in self.search.return_value if row["chunk_id"] == identifier), None))

    def proposal(self, verdict="needs_review"):
        return {"assessment": {"verdict": verdict, "explanation": "Evidence must support claims.",
            "weaknesses": ["Confusing keyword overlap with support."] if verdict == "needs_review"
            else [], "citations": [{"source_id": "S1", "quote": QUOTE}]},
            "adjustments": [{"task_id": self.plan["tasks"][1]["id"], "title": "Check support",
                "study_goal": "Compare keyword overlap and real support.",
                "quiz_question": "Does shared terminology guarantee support?",
                "quiz_answer": "No, evidence must support the claim.",
                "citation": {"source_id": "S1", "quote": QUOTE}}]
            if verdict == "needs_review" else []}

    def agent(self, proposal=None, outputs=None):
        self.calls = Mock(side_effect=outputs or [
            response("search_knowledge", {"query": "support"}),
            response("propose_feedback", proposal or self.proposal())])
        client = SimpleNamespace(responses=SimpleNamespace(create=self.calls))
        return FeedbackAgent(self.config, self.store, client, self.base)

    def run_feedback(self, agent=None):
        return (agent or self.agent()).run(self.plan["id"], self.task["id"], self.request)

    def test_feedback_drafts_without_changing_any_task_then_human_confirmation_applies(self):
        before = self.plan["tasks"]
        result = self.run_feedback()
        self.assertEqual(result["tasks"], before)
        feedback = result["feedback"][0]
        self.assertEqual(feedback["status"], "draft")
        self.assertEqual(feedback["assessment"]["verdict"], "needs_review")
        self.assertEqual(feedback["api_calls"], 2)
        self.assertGreater(feedback["accounted_cost_usd"], 0)
        self.assertEqual(self.search.call_args.args[:2], ("DEMO101", "all"))
        updated = self.store.confirm_feedback(result["id"], feedback["id"], result["version"])
        self.assertEqual(updated["tasks"][0], before[0])
        self.assertEqual(updated["tasks"][2], before[2])
        self.assertEqual(updated["tasks"][1]["title"], "Check support")
        self.assertEqual(updated["feedback"][0]["applied_plan_version"], updated["version"])
        self.assertTrue(updated["feedback"][0]["confirmed_at"])
        for key in ("id", "day", "minutes", "done"):
            self.assertEqual(updated["tasks"][1][key], before[1][key])
        self.assertEqual(self.calls.call_count, 2)  # Confirmation does not call model.
        reopened = StudyStore(self.store.path).get(result["id"])
        self.assertEqual(reopened["tasks"], updated["tasks"])

    def test_changed_source_blocks_feedback_confirmation_without_touching_progress(self):
        result = self.run_feedback()
        feedback = result["feedback"][0]
        self.assertEqual(feedback["verification"]["status"], "passed")
        self.base.source = lambda _: None
        with self.assertRaises(StudyConflict):
            self.store.confirm_feedback(result["id"], feedback["id"], result["version"],
                verifier=lambda parent, child: check_feedback(parent, child, self.base.source))
        self.assertEqual(self.store.get(result["id"])["tasks"], self.plan["tasks"])
        self.assertEqual(self.calls.call_count, 2)

    def test_progress_changed_during_generation_blocks_postcheck_and_preserves_completed_task(self):
        agent = self.agent()
        outputs = iter([response("search_knowledge", {"query": "evidence"}),
                        response("propose_feedback", self.proposal())])

        def respond(**kwargs):
            result = next(outputs)
            if result.output[0].name == "propose_feedback":
                current = self.store.get(self.plan["id"])
                self.store.set_task(current["id"], current["tasks"][1]["id"], True,
                                    current["version"])
            return result

        self.calls.side_effect = respond
        result = self.run_feedback(agent)
        self.assertTrue(result["tasks"][1]["done"])
        feedback = result["feedback"][0]
        self.assertEqual(feedback["status"], "failed")
        self.assertEqual(feedback["error_code"], "postcheck_failed")
        self.assertEqual(feedback["verification"]["status"], "blocked")
        self.assertEqual(feedback["adjustments"], [])

    def test_idempotent_submission_and_confirmation_never_repeat_calls_or_edits(self):
        agent = self.agent()
        one = self.run_feedback(agent)
        two = self.run_feedback(agent)
        self.assertEqual(one, two)
        self.assertEqual(self.calls.call_count, 2)
        feedback = one["feedback"][0]
        active = self.store.confirm_feedback(one["id"], feedback["id"], one["version"])
        replay = self.store.confirm_feedback(one["id"], feedback["id"], one["version"])
        self.assertEqual(active["version"], replay["version"])
        with self.assertRaises(StudyConflict):
            agent.run(self.plan["id"], self.task["id"], self.request.model_copy(
                update={"answer": "Changed answer, same request ID."}))

    def test_concurrent_same_submission_starts_only_one_metered_run(self):
        def start(_):
            try:
                return self.store.begin_feedback(self.plan["id"], self.task["id"], self.request)[1]
            except StudyConflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(start, [0, 1])), 1)

    def test_progress_change_invalidates_adjustment_but_preserves_feedback(self):
        result = self.run_feedback()
        changed = self.store.set_task(result["id"], self.plan["tasks"][2]["id"], True,
                                      result["version"])
        with self.assertRaises(StudyConflict):
            self.store.confirm_feedback(result["id"], result["feedback"][0]["id"],
                                         changed["version"])
        self.assertTrue(self.store.get(result["id"])["tasks"][2]["done"])

    def test_uncertain_feedback_never_creates_weaknesses_or_plan_edits(self):
        result = self.run_feedback(self.agent(self.proposal("uncertain")))
        feedback = result["feedback"][0]
        self.assertEqual(feedback["assessment"]["verdict"], "uncertain")
        self.assertEqual(feedback["adjustments"], [])
        self.assertEqual(feedback["assessment"]["weaknesses"], [])
        with self.assertRaises(StudyConflict):
            self.store.confirm_feedback(result["id"], feedback["id"], result["version"])

    def test_missing_evidence_returns_uncertain_without_second_model_call(self):
        self.search.return_value = []
        result = self.run_feedback()
        self.assertEqual(result["feedback"][0]["assessment"]["verdict"], "uncertain")
        self.assertEqual(self.calls.call_count, 1)

    def test_rejects_completed_current_foreign_and_duplicate_task_edits(self):
        for bad_id in (self.task["id"], uuid.uuid4().hex, self.plan["tasks"][2]["id"]):
            with self.subTest(task=bad_id):
                proposal = self.proposal()
                proposal["adjustments"][0]["task_id"] = bad_id
                if bad_id == self.plan["tasks"][2]["id"]:
                    self.plan = self.store.set_task(self.plan["id"], bad_id, True,
                                                    self.store.get(self.plan["id"])["version"])
                request = self.request.model_copy(update={"request_id": uuid.uuid4(),
                    "version": self.store.get(self.plan["id"])["version"]})
                result = self.agent(proposal).run(self.plan["id"], self.task["id"], request)
                self.assertEqual(result["feedback"][-1]["status"], "failed")
                self.assertEqual(result["feedback"][-1]["adjustments"], [])

    def test_rejects_wrong_tool_invented_quote_and_uncertain_replan(self):
        scenarios = []
        bad = self.proposal()
        bad["assessment"]["citations"][0]["quote"] = "Invented source text"
        scenarios.append([response("search_knowledge", {"query": "support"}),
                          response("propose_feedback", bad)])
        bad = self.proposal()
        bad["assessment"]["verdict"] = "uncertain"
        scenarios.append([response("search_knowledge", {"query": "support"}),
                          response("propose_feedback", bad)])
        scenarios.append([response("confirm_feedback", {"confirmed": True})])
        for outputs in scenarios:
            request = self.request.model_copy(update={"request_id": uuid.uuid4(),
                "version": self.store.get(self.plan["id"])["version"]})
            result = self.agent(outputs=outputs).run(self.plan["id"], self.task["id"], request)
            self.assertEqual(result["feedback"][-1]["status"], "failed")
            self.assertEqual(result["feedback"][-1]["adjustments"], [])

    def test_feedback_cannot_be_confirmed_using_original_plan_endpoint(self):
        result = self.run_feedback()
        feedback = result["feedback"][0]
        with self.assertRaises(StudyConflict):
            self.store.confirm(feedback["id"], feedback["version"])
        self.assertEqual([p["id"] for p in self.store.list("DEMO101")], [self.plan["id"]])

    def test_budget_exception_keeps_original_tasks_and_records_failure(self):
        self.store.reserve = Mock(side_effect=StudyBudgetExceeded("SECRET provider detail"))
        result = self.run_feedback()
        self.assertEqual(result["tasks"], self.plan["tasks"])
        self.assertEqual(result["feedback"][0]["error_code"], "budget_limit")
        self.assertNotIn("SECRET", json.dumps(result))
        self.calls.assert_not_called()

    def test_duplicate_edit_and_model_injected_progress_fields_are_rejected(self):
        duplicate = self.proposal()
        duplicate["adjustments"].append(dict(duplicate["adjustments"][0]))
        result = self.run_feedback(self.agent(duplicate))
        self.assertEqual(result["feedback"][-1]["status"], "failed")
        injected = self.proposal()
        injected["adjustments"][0]["done"] = True
        request = self.request.model_copy(update={"request_id": uuid.uuid4(),
            "version": result["version"]})
        result = self.agent(injected).run(self.plan["id"], self.task["id"], request)
        self.assertEqual(result["feedback"][-1]["status"], "failed")
        self.assertEqual(result["tasks"], self.plan["tasks"])

    def test_local_api_submits_answer_and_requires_true_confirmation(self):
        agent = self.agent()
        app.dependency_overrides[get_settings] = lambda: self.config
        app.dependency_overrides[get_study_store] = lambda: self.store
        app.dependency_overrides[get_feedback_agent] = lambda: agent
        app.dependency_overrides[get_hybrid_base] = lambda: self.base
        self.addCleanup(app.dependency_overrides.clear)
        client = TestClient(app)
        url = f"/api/study-plans/{self.plan['id']}"
        response = client.post(url + f"/tasks/{self.task['id']}/answers",
                               json=self.request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        target = url + f"/feedbacks/{result['feedback'][0]['id']}/confirm"
        self.assertEqual(client.post(target, json={
            "confirmed": False, "version": result["version"]}).status_code, 422)
        applied = client.post(target, json={"confirmed": True, "version": result["version"]})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["tasks"][1]["title"], "Check support")
        self.assertEqual(client.post(url + "/tasks/missing/answers",
            json={**self.request.model_dump(mode="json"), "request_id": str(uuid.uuid4()),
                  "version": applied.json()["version"]}).status_code, 404)

    def test_unknown_provider_failure_retains_reservation_and_redacts_exception(self):
        agent = self.agent()
        self.calls.side_effect = TimeoutError("provider SECRET with private request")
        result = self.run_feedback(agent)
        feedback = result["feedback"][0]
        self.assertEqual(feedback["status"], "failed")
        self.assertEqual(feedback["uncertain_calls"], 1)
        self.assertEqual(feedback["accounted_cost_usd"], 0.02)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_submission_requires_active_current_plan_before_any_paid_call(self):
        agent = self.agent()
        self.store.set_task(self.plan["id"], self.task["id"], True, self.plan["version"])
        with self.assertRaises(StudyConflict):
            self.run_feedback(agent)
        self.calls.assert_not_called()
        self.search.assert_not_called()


if __name__ == "__main__":
    unittest.main()

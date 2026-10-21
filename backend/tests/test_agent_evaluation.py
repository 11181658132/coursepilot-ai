import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.agent_evaluation import (
    DATASET,
    QUOTE,
    evaluate,
    grade_plan,
    load_scenarios,
    main,
    render_report,
    summarise,
)
from app.services.study_agent import PlanRequest


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.request = PlanRequest(request_id=uuid.uuid4(), course="DEMO101", goal="Review RAG",
                                   days=1, daily_minutes=45)
        self.passages = {"c1": {"source_id": "S1", "path": "DEMO101/lecture/fixture.md",
                                 "text": QUOTE}}
        self.plan = {"tasks": [{"day": 1, "minutes": 45, "quiz_question": "What is RAG?",
                                "quiz_answer": "Retrieval with generation.", "citations": [{
                                    "chunk_id": "c1", "source_id": "S1", "quote": QUOTE,
                                    "path": "DEMO101/lecture/fixture.md"}]}]}

    def test_dataset_is_strict_unique_and_has_all_three_categories(self):
        cases = load_scenarios()
        self.assertEqual(len(cases), 30)
        self.assertEqual({case.category for case in cases}, {"completion", "state", "safety"})
        original = json.loads(DATASET.read_text())
        for mutation in ("duplicate", "unknown_kind", "extra"):
            dataset = copy.deepcopy(original)
            if mutation == "duplicate":
                dataset["cases"].append(dataset["cases"][0])
            elif mutation == "unknown_kind":
                dataset["cases"][0]["kind"] = "run_external_command"
            else:
                dataset["cases"][0]["external_url"] = "https://example.com"
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "cases.json"
                # Generated test data, never user files.
                path.write_text(json.dumps(dataset))
                with self.assertRaises(ValueError):
                    load_scenarios(path)

    def test_quote_grader_has_independent_negative_controls(self):
        self.assertTrue(all(grade_plan(self.plan, self.request, self.passages)["checks"].values()))
        for key, value in (("quote", "A fabricated quote that does not exist."),
                           ("source_id", "S99"), ("chunk_id", "unknown"),
                           ("path", "OTHER/private.md")):
            changed = copy.deepcopy(self.plan)
            changed["tasks"][0]["citations"][0][key] = value
            result = grade_plan(changed, self.request, self.passages)
            self.assertFalse(result["checks"]["quotes_match_sources"])
            self.assertEqual(result["matching_quotes"], 0)

    def test_missing_citations_and_wrong_days_are_not_given_credit(self):
        changed = copy.deepcopy(self.plan)
        changed["tasks"][0]["citations"] = []
        changed["tasks"][0]["day"] = 2
        result = grade_plan(changed, self.request, self.passages)
        self.assertFalse(result["checks"]["day_coverage"])
        self.assertFalse(result["checks"]["citations_present"])
        self.assertFalse(result["checks"]["quotes_match_sources"])

    def test_quote_presence_never_claims_semantic_correctness(self):
        self.plan["tasks"][0]["quiz_answer"] = "Unsupported but syntactically valid claim"
        result = grade_plan(self.plan, self.request, self.passages)
        self.assertTrue(result["checks"]["quotes_match_sources"])
        self.assertIsNone(result["semantic_correctness"])
        self.assertIsNone(result["claim_support"])
        self.assertEqual(result["human_reviewed"], 0)

    def test_full_scripted_suite_is_free_and_reports_explicit_denominators(self):
        report = evaluate()
        summary = report["summary"]
        self.assertEqual(summary["contract_pass"], {"numerator": 30, "denominator": 30, "rate": 1})
        self.assertEqual(summary["scripted_completion"]["denominator"], 5)
        self.assertEqual(summary["by_category"]["safety"]["denominator"], 21)
        self.assertEqual(summary["external_api_calls"], 0)
        self.assertEqual(summary["actual_additional_api_cost_usd"], 0)
        self.assertIsNone(summary["model_task_success_rate"])
        self.assertIsNone(summary["live_inference_latency_ms"])
        self.assertEqual(summary["displayed_quote_match"]["denominator"], 32)
        self.assertEqual(summary["harness_errors"], 0)

    def test_empty_denominators_are_unknown_not_perfect_scores(self):
        summary = summarise([])
        self.assertIsNone(summary["contract_pass"]["rate"])
        self.assertIsNone(summary["displayed_quote_match"]["rate"])
        self.assertIsNone(summary["scripted_completion"]["rate"])

    def test_wrong_expected_result_fails_instead_of_self_grading_pass(self):
        case = load_scenarios()[0].model_copy(update={"status": "failed", "error": "agent_limit"})
        report = evaluate([case])
        self.assertEqual(report["summary"]["contract_pass"]["numerator"], 0)

    def test_harness_exception_is_isolated_and_not_hidden_as_success(self):
        cases = load_scenarios()[:2]
        with patch("app.agent_evaluation.run_case", side_effect=RuntimeError("sk-secret input")):
            report = evaluate(cases)
        self.assertEqual(report["summary"]["harness_errors"], 2)
        self.assertEqual(report["summary"]["contract_pass"]["numerator"], 0)
        self.assertIsNone(report["summary"]["scripted_sdk_calls"])
        self.assertNotIn("sk-secret", json.dumps(report))

    def test_html_escapes_report_data(self):
        report = evaluate(load_scenarios()[:1])
        report["results"][0]["id"] = "<script>alert(1)</script>"
        rendered = render_report(report)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_cli_regression_fails_ci_and_keeps_archived_reports(self):
        case = load_scenarios()[0].model_copy(update={"status": "failed", "error": "agent_limit"})
        report = evaluate([case])
        with tempfile.TemporaryDirectory() as directory:
            with (patch("app.agent_evaluation.PROJECT_ROOT", Path(directory)),
                  patch("app.agent_evaluation.evaluate", return_value=report),
                  patch("sys.argv", ["agent-evaluation"]), patch("builtins.print")):
                for _ in range(2):
                    with self.assertRaises(SystemExit) as stopped:
                        main()
                    self.assertEqual(stopped.exception.code, 1)
            root = Path(directory) / "evals/reports/agent-contract"
            self.assertEqual(len(list(root.glob("*/report.json"))), 2)
            self.assertTrue((root / "latest.md").is_file())


if __name__ == "__main__":
    unittest.main()

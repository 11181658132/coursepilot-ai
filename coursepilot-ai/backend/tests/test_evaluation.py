import tempfile
import unittest
from pathlib import Path

from app.evaluation import EvalCase, EvalResult, score_case, summarise, write_report
from app.models import ChatResponse, Citation, Evidence


class EvaluationTests(unittest.TestCase):
    def test_scores_grounded_answer(self) -> None:
        case = EvalCase(
            id="knn",
            course="90049",
            material_type="lecture",
            question="How does KNN work?",
            expected_files=["03-knn.pdf"],
            expected_keywords=["distance", "neighbor"],
        )
        response = ChatResponse(
            answer="It uses distance to select the nearest neighbor.",
            citations=[Citation(filename="03-knn.pdf")],
            evidence=[Evidence(filename="03-knn.pdf", score=0.9)],
            latency_ms=200,
            estimated_cost_usd=0.001,
        )
        result = score_case(case, response)
        self.assertTrue(result.passed)
        self.assertTrue(result.expected_file_hit)
        self.assertEqual(result.keyword_recall, 1.0)

    def test_scores_expected_refusal(self) -> None:
        case = EvalCase(
            id="missing",
            course="90049",
            material_type="lecture",
            question="What room?",
            expected_files=[],
            expected_keywords=[],
            should_refuse=True,
        )
        response = ChatResponse(answer="The files do not contain enough information.")
        self.assertTrue(score_case(case, response).passed)

    def test_writes_json_and_html_reports(self) -> None:
        response = ChatResponse(
            answer="distance neighbor",
            citations=[Citation(filename="03-knn.pdf")],
            evidence=[Evidence(filename="03-knn.pdf")],
        )
        result = score_case(
            EvalCase("a", "90049", "lecture", "question", ["03-knn.pdf"], ["distance"]),
            response,
        )
        report = {
            "generated_at": "2026-10-11T00:00:00+00:00",
            "summary": summarise([result]),
            "results": [result.__dict__],
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(report, Path(directory))
            self.assertTrue(paths["json"].is_file())
            self.assertIn("CoursePilot AI Evaluation", paths["html"].read_text())

    def test_summary_separates_blocked_cases_from_quality_failures(self) -> None:
        blocked = EvalResult(
            id="blocked", course="90049", material_type="lecture", question="q",
            passed=False, answer="", cited_files=[], retrieved_files=[],
            expected_file_hit=None, citation_precision=None, keyword_recall=None,
            refusal_detected=False, latency_ms=0, estimated_cost_usd=0,
            error="RateLimitError",
        )
        summary = summarise([blocked])
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["failed"], 0)


if __name__ == "__main__":
    unittest.main()

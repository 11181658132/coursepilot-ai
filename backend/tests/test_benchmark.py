import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from app.benchmark import compare, resolve_public_store, validate_dataset, verify_hosted_corpus
from app.config import PROJECT_ROOT
from app.eval_budget import BudgetClient, BudgetExceeded, BudgetLedger
from app.evaluation import (
    EvalCase,
    apply_reviews,
    load_cases,
    result_hash,
    resume_evaluation,
    run_evaluation,
    score_case,
    summarise,
    write_report,
    write_review_template,
)
from app.models import ChatResponse, Citation, Evidence
from openai import NotFoundError


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.case = EvalCase("case", "DEMO101", "lecture", "What is RAG?",
                             ["a.md"], ["retrieval"])
        self.response = ChatResponse(answer="Retrieval supplies evidence.",
                                     citations=[Citation(filename="a.md")],
                                     evidence=[Evidence(filename="a.md", text="original text")])

    def report(self):
        result = score_case(self.case, self.response)
        return {"generated_at": "2026-10-21T00:00:00Z", "scoring_version": 2,
                "fingerprint": "same-config", "summary": summarise([result]),
                "results": [asdict(result)]}

    def test_all_50_fictional_cases_have_valid_source_anchors_and_frozen_splits(self):
        cases = validate_dataset()
        self.assertEqual(len(cases), 50)
        self.assertEqual(sum(case.split == "holdout" for case in cases), 20)
        self.assertEqual({case.course for case in cases}, {"DEMO101"})
        self.assertTrue(all(case.label_status == "draft_source_checked" for case in cases))

    def hosted_client(self, tampered=False):
        sources = sorted((PROJECT_ROOT / "frontend/demo-knowledge").glob("*.md"))
        mapping = {str(i): source for i, source in enumerate(sources)}
        items = [SimpleNamespace(id=key, status="completed", attributes={
            "course": "DEMO101", "material_type": "tutorial"
            if source.name.startswith("tutorial") else "lecture"})
            for key, source in mapping.items()]

        def content(file_id, **kwargs):
            text = mapping[file_id].read_text()
            text = text.replace("RAG", "ALTERED") if tampered else "  " + text.replace("\n", " ")
            return [SimpleNamespace(text=text, type="text")]

        return SimpleNamespace(vector_stores=SimpleNamespace(files=SimpleNamespace(
            list=lambda **kwargs: items, content=content)), files=SimpleNamespace(
                retrieve=lambda file_id: SimpleNamespace(filename=mapping[file_id].name),
                content=Mock(side_effect=AssertionError("Must not download assistants originals"))))

    def test_hosted_source_verification_uses_parsed_content_not_original_download(self):
        client = self.hosted_client()
        verify_hosted_corpus(client, "fictional-store")
        client.files.content.assert_not_called()

    def test_hosted_source_verification_rejects_changed_nonwhitespace_content(self):
        with self.assertRaises(ValueError):
            verify_hosted_corpus(self.hosted_client(tampered=True), "fictional-store")

    def test_stale_store_can_only_fall_back_to_one_exact_name_existing_public_store(self):
        response = httpx.Response(404, request=httpx.Request("GET", "https://api.openai.com"))
        error = NotFoundError("not found", response=response, body={})
        client = SimpleNamespace(vector_stores=SimpleNamespace(list=Mock(return_value=[
            SimpleNamespace(name="CoursePilot Public Demo", id="existing-public") ])))
        with patch("app.benchmark.verify_hosted_corpus", side_effect=[error, None]) as verify:
            self.assertEqual(resolve_public_store(client, "stale"), "existing-public")
            self.assertEqual(verify.call_args.args[1], "existing-public")
        client.vector_stores.list.return_value = []
        with patch("app.benchmark.verify_hosted_corpus", side_effect=error):
            with self.assertRaises(ValueError):
                resolve_public_store(client, "stale")

    def test_citation_alone_is_not_a_retrieval_hit(self):
        self.response.evidence = []
        result = score_case(self.case, self.response)
        self.assertFalse(result.expected_file_hit)
        self.assertFalse(result.passed)
        self.assertIn("citation_not_retrieved", result.failure_categories)

    def test_missing_citations_are_zero_precision_not_dropped(self):
        self.response.citations = []
        result = score_case(self.case, self.response)
        self.assertEqual(result.citation_precision, 0)
        self.assertEqual(summarise([result])["citation_precision"], 0)

    def test_irrelevant_extra_citation_fails_proxy(self):
        self.response.citations.append(Citation(filename="wrong.md"))
        result = score_case(self.case, self.response)
        self.assertEqual(result.citation_precision, 0.5)
        self.assertIn("citation_irrelevant", result.failure_categories)

    def test_multi_source_questions_require_all_sources(self):
        self.case.expected_files.append("b.md")
        self.case.require_all_sources = True
        result = score_case(self.case, self.response)
        self.assertTrue(result.expected_file_hit)
        self.assertEqual(result.retrieval_recall, 0.5)
        self.assertIn("retrieval_incomplete", result.failure_categories)
        self.assertIn("citation_incomplete", result.failure_categories)

    def test_reciprocal_rank_uses_first_relevant_retrieved_document(self):
        self.response.evidence.insert(0, Evidence(filename="wrong.md"))
        self.assertEqual(score_case(self.case, self.response).reciprocal_rank, 0.5)

    def test_quote_is_checked_against_same_file_evidence(self):
        self.response.citations[0].quote = "invented quote"
        result = score_case(self.case, self.response)
        self.assertEqual(result.quote_validity, 0)
        self.assertIn("invalid_quote", result.failure_categories)
        self.response.citations[0].quote = "original text"
        self.assertEqual(score_case(self.case, self.response).quote_validity, 1)

    def test_injection_canary_fails_even_when_refusal_phrase_present(self):
        self.case.should_refuse = True
        self.case.forbidden_keywords = ["EVAL_CANARY"]
        self.response.answer = "Cannot provide that secret. EVAL_CANARY"
        result = score_case(self.case, self.response)
        self.assertTrue(result.refusal_detected)
        self.assertFalse(result.passed)
        self.assertIn("forbidden_output", result.failure_categories)

    def test_refusal_discussion_with_valid_citations_is_not_automatically_refusal(self):
        self.response.answer = "Retrieval should abstain when there is not enough evidence."
        self.assertTrue(score_case(self.case, self.response).passed)

    def test_percentiles_and_coverage_are_separate_from_completed_pass_rate(self):
        results = []
        for latency in range(1, 21):
            self.response.latency_ms = latency * 100
            results.append(score_case(self.case, self.response))
        report = run_evaluation(SimpleNamespace(chat=Mock(side_effect=RuntimeError("secret"))),
                                [self.case])
        from app.evaluation import EvalResult
        results.append(EvalResult(**report["results"][0]))
        summary = summarise(results)
        self.assertEqual(summary["p50_latency_ms"], 1000)
        self.assertEqual(summary["p95_latency_ms"], 1900)
        self.assertEqual(summary["pass_rate"], 1)
        self.assertLess(summary["overall_pass_rate"], 1)
        self.assertLess(summary["coverage"], 1)
        self.assertNotIn("secret", json.dumps(report))

    def test_review_templates_never_pretend_to_be_human_grades(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.jsonl"
            report = self.report()
            write_review_template(report, path)
            self.assertEqual(apply_reviews(report, path)["reviewed"], 0)
            self.assertIsNone(apply_reviews(report, path)["faithfulness"])
            with self.assertRaises(FileExistsError):
                write_review_template(report, path)
            row = json.loads(path.read_text())
            row.update(correct=True, supported=False, reviewer="human-example")
            path.write_text(json.dumps(row))
            metrics = apply_reviews(report, path)
            self.assertEqual(metrics["correctness"], 1)
            self.assertEqual(metrics["faithfulness"], 0)

    def test_changed_answers_cannot_reuse_human_labels(self):
        report = self.report()
        original = result_hash(report, report["results"][0])
        report["results"][0]["answer"] = "Different output"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            path.write_text(json.dumps({"id": "case", "response_hash": original,
                                       "correct": True, "supported": True, "reviewer": "user"}))
            with self.assertRaises(ValueError):
                apply_reviews(report, path)

    def test_resume_rejects_old_metric_versions_and_modified_questions(self):
        with self.assertRaises(ValueError):
            resume_evaluation(None, [self.case], {"results": []})
        report = self.report()
        self.case.question = "Changed question"
        with self.assertRaises(ValueError):
            resume_evaluation(None, [self.case], report)

    def test_comparison_uses_only_common_completed_cases_and_matching_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for variant in ("file_search", "hybrid-no-rerank", "hybrid"):
                report = self.report()
                if variant == "file_search":
                    report["results"][0]["error"] = "RateLimitError"
                write_report(report, root / variant)
            self.assertEqual(compare(root)["paired_case_count"], 0)
            report["fingerprint"] = "different"
            write_report(report, root / "hybrid")
            with self.assertRaises(ValueError):
                compare(root)

    def test_case_loader_rejects_duplicate_ids_and_empty_keyword_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            row = asdict(self.case)
            path.write_text(json.dumps(row) + "\n" + json.dumps(row))
            with self.assertRaises(ValueError):
                load_cases(path)
            row["expected_keywords"] = [[]]
            path.write_text(json.dumps(row))
            with self.assertRaises(ValueError):
                load_cases(path)


class BudgetTests(unittest.TestCase):
    def test_crash_reservations_survive_resume_and_prevent_overspend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            ledger = BudgetLedger(path, 0.03)
            with ledger.lock():
                ledger.reserve(0.02)
            reopened = BudgetLedger(path, 0.03)
            with reopened.lock(), self.assertRaises(BudgetExceeded):
                reopened.reserve(0.02)
            self.assertEqual(reopened.data["uncertain_calls"], 1)

    def test_settlement_counts_actual_usage_and_releases_unused_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = BudgetLedger(Path(directory) / "budget.json", 0.50)
            ledger.reserve(0.02)
            ledger.settle(0.02, 0.001)
            self.assertAlmostEqual(ledger.data["accounted_usd"], 0.001)
            self.assertEqual(ledger.data["uncertain_calls"], 0)

    def test_invalid_or_unapproved_budget_is_rejected(self):
        for value in (0, -1, float("nan"), 0.51):
            with self.assertRaises(ValueError):
                BudgetLedger(Path("unused.json"), value)

    def test_paid_calls_are_capped_and_sdk_errors_keep_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = BudgetLedger(Path(directory) / "budget.json", 0.50)
            create = Mock(side_effect=RuntimeError("provider body"))
            sdk = SimpleNamespace(responses=SimpleNamespace(create=create))
            client = BudgetClient(sdk, ledger)
            with self.assertRaises(RuntimeError):
                client.responses.create(model="gpt-6-luna", input="small", tools=[{
                    "type": "file_search"}])
            sent = create.call_args.kwargs
            self.assertEqual(sent["max_output_tokens"], 1500)
            self.assertEqual(sent["max_tool_calls"], 1)
            self.assertEqual(sent["service_tier"], "default")
            self.assertFalse(sent["store"])
            self.assertEqual(ledger.data["accounted_usd"], 0.02)
            with self.assertRaises(ValueError):
                client.responses.create(model="other-model", input="test")
            self.assertEqual(create.call_count, 1)


if __name__ == "__main__":
    unittest.main()

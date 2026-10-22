import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from app.benchmark import DATASET, VARIANTS, corpus_hash
from app.evaluation import SCORING_VERSION, load_cases, score_case
from app.models import ChatResponse
from app.quality_review import (
    build_packet,
    diagnose,
    import_labels,
    load_suite,
    main,
    prepare,
    render_review,
)


class QualityReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.suite = self.root / "suite"
        case = next(c for c in load_cases(DATASET) if c.split == "dev")
        row = asdict(score_case(case, ChatResponse(answer="Not enough evidence.")))
        for name in VARIANTS:
            directory = self.suite / name
            directory.mkdir(parents=True)
            report = {"variant": name, "fingerprint": "test-comparable-experiment",
                      "scoring_version": SCORING_VERSION, "metadata": {
                          "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                          "corpus_sha256": corpus_hash(), "split": "dev", "case_ids": [case.id]},
                      "results": [row]}
            (directory / "latest.json").write_text(json.dumps(report))
        self.packet_dir = self.root / "packet"
        self.labels = self.root / "labels.jsonl"

    def change(self, variant, mutate):
        path = self.suite / variant / "latest.json"
        report = json.loads(path.read_text())
        mutate(report)
        path.write_text(json.dumps(report))

    def label_rows(self):
        reports, cases = load_suite(self.suite)
        packet, mapping = build_packet(reports, cases)
        return [{"review_key": r["review_key"], "response_hash": r["response_hash"],
                 "correct": False, "supported": True, "reviewer": "unit-test-human-fixture",
                 "human_attested": True, "notes": "Synthetic unit-test label, not real review.",
                 "failure_stage": "answer"} for r in packet["rows"]], mapping

    def write_labels(self, rows):
        self.labels.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_prepare_is_offline_blinded_and_does_not_overwrite_history(self):
        before = {p: p.read_bytes() for p in self.suite.rglob("*.json")}
        result = prepare(self.suite, self.packet_dir)
        self.assertEqual(result, {"review_rows": 3, "paired_cases": 1,
                                  "api_calls": 0, "human_reviews_created": 0})
        page = (self.packet_dir / "review.html").read_text()
        self.assertNotIn('"variant"', page)
        self.assertNotIn('"passed"', page)
        self.assertNotIn('"rerank_score"', page)
        self.assertIn("connect-src 'none'", page)
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data)
        with self.assertRaises(FileExistsError):
            prepare(self.suite, self.packet_dir)

    def test_reject_holdout_private_and_stale_cases(self):
        for field, value in (("split", "holdout"), ("course", "PRIVATE"),
                             ("question", "changed"), ("reference_answer", "changed")):
            with self.subTest(field=field):
                path = self.suite / "hybrid/latest.json"
                original = path.read_text()
                self.change("hybrid", lambda d, f=field, v=value: d["results"][0].update({f: v}))
                with self.assertRaises(ValueError):
                    load_suite(self.suite)
                path.write_text(original)

    def test_reject_mismatched_fingerprints_and_missing_results(self):
        self.change("hybrid", lambda d: d.update(fingerprint="other"))
        with self.assertRaises(ValueError):
            load_suite(self.suite)
        self.change("hybrid", lambda d: d.update(
            fingerprint="test-comparable-experiment", results=[]))
        with self.assertRaises(ValueError):
            load_suite(self.suite)

    def test_proxy_diagnosis_does_not_invent_semantic_grades(self):
        reports, _ = load_suite(self.suite)
        reports["hybrid"]["results"][0]["passed"] = True
        diagnosis = diagnose(reports)
        self.assertEqual(diagnosis["reranking_paired_proxy_transitions"], {"0->1": 1})
        self.assertIsNone(diagnosis["semantic_correctness"])
        self.assertIsNone(diagnosis["semantic_faithfulness"])

    def test_initial_analysis_has_null_human_metrics_and_no_invented_repair_items(self):
        prepare(self.suite, self.packet_dir)
        analysis = json.loads((self.packet_dir / "review-analysis.json").read_text())
        self.assertEqual(analysis["status"], "awaiting_human_reviews")
        self.assertEqual(analysis["reviewed_outputs"], 0)
        self.assertEqual(analysis["eligible_outputs"], 3)
        self.assertEqual(analysis["repair_queue"], [])
        for values in analysis["variants"].values():
            self.assertEqual(values["reviewed_subset"]["correctness"], {
                "numerator": 0, "denominator": 0, "rate": None})
        self.assertIn("Historical cached responses", analysis["limitations"][0])

    def test_human_analysis_counts_failures_categories_and_proxy_disagreement(self):
        prepare(self.suite, self.packet_dir)
        rows, mapping = self.label_rows()
        failed_variant = mapping[rows[0]["review_key"]]["variant"]
        rows[1].update(correct=True, supported=True, failure_stage="none", notes="")
        rows[2].update(correct=True, supported=True, failure_stage="reference_label",
                       notes="Synthetic reference dispute.")
        self.write_labels(rows)
        output = self.root / "analysed"
        import_labels(self.suite, self.packet_dir, self.labels, output)
        analysis = json.loads((output / "review-analysis.json").read_text())
        self.assertEqual(analysis["status"], "complete_development_review")
        self.assertEqual(analysis["paired_reviewed_cases"], 1)
        self.assertEqual(len(analysis["repair_queue"]), 2)  # Failure + reference dispute.
        values = analysis["variants"][failed_variant]
        self.assertEqual(values["reviewed_subset"]["correctness"]["denominator"], 1)
        self.assertEqual(values["reviewed_subset"]["both"]["numerator"], 0)
        self.assertEqual(values["reviewed_subset"]["faithfulness"]["rate"], 1)
        self.assertEqual(values["proxy_vs_human_both"]["proxy_fail_human_fail"], 1)
        category = next(iter(values["categories"].values()))
        self.assertEqual(category["eligible_outputs"], 1)
        self.assertEqual(category["correctness"]["rate"], 0)
        markdown = (output / "review-analysis.md").read_text()
        self.assertNotIn("Synthetic reference dispute", markdown)
        self.assertIn("1/1", markdown)

    def test_blocked_outputs_are_not_counted_as_human_quality_failures(self):
        self.change("hybrid", lambda d: d["results"][0].update(error="Synthetic API failure"))
        prepare(self.suite, self.packet_dir)
        analysis = json.loads((self.packet_dir / "review-analysis.json").read_text())
        self.assertEqual(analysis["eligible_outputs"], 2)
        values = analysis["variants"]["hybrid"]
        self.assertEqual(values["blocked_outputs"], 1)
        self.assertEqual(values["reviewed_subset"]["both"]["denominator"], 0)
        self.assertIsNone(values["reviewed_subset"]["both"]["rate"])

    def test_partial_analysis_keeps_paired_denominator_zero(self):
        prepare(self.suite, self.packet_dir)
        rows, _ = self.label_rows()
        self.write_labels(rows[:1])
        output = self.root / "partial-analysis"
        import_labels(self.suite, self.packet_dir, self.labels, output)
        analysis = json.loads((output / "review-analysis.json").read_text())
        self.assertEqual(analysis["status"], "partial")
        self.assertEqual(analysis["reviewed_outputs"], 1)
        for values in analysis["variants"].values():
            self.assertIsNone(values["paired_subset"]["both"]["rate"])

    def test_rejects_unknown_fields_oversized_and_contradictory_labels(self):
        prepare(self.suite, self.packet_dir)
        rows, _ = self.label_rows()
        for change in ({"extra": "unapproved data"}, {"reviewer": "x" * 81},
                       {"notes": "x" * 5001}, {"failure_stage": "none"}):
            with self.subTest(change=list(change)):
                self.write_labels([{**rows[0], **change}])
                with self.assertRaises(ValueError):
                    import_labels(self.suite, self.packet_dir, self.labels, self.root / "invalid")

    def test_review_navigation_does_not_fill_or_attest_grades(self):
        prepare(self.suite, self.packet_dir)
        page = (self.packet_dir / "review.html").read_text()
        self.assertIn('id="unreviewed"', page)
        self.assertIn('id="jump"', page)
        self.assertIn("整题完成", page)
        self.assertNotIn("localStorage", page)
        self.assertNotIn("sessionStorage", page)

    def test_import_binds_exact_outputs_and_preserves_original_report(self):
        prepare(self.suite, self.packet_dir)
        rows, _ = self.label_rows()
        self.write_labels(rows)
        result = import_labels(self.suite, self.packet_dir, self.labels, self.root / "imported")
        self.assertEqual(result["paired_reviewed_cases"], 1)
        for values in result["variants"].values():
            self.assertEqual(values["paired_correctness"], 0)
            self.assertEqual(values["paired_faithfulness"], 1)
        original = json.loads((self.suite / "hybrid/latest.json").read_text())
        self.assertNotIn("human_review", original)

    def test_partial_review_cannot_be_reported_as_paired_accuracy(self):
        prepare(self.suite, self.packet_dir)
        rows, _ = self.label_rows()
        self.write_labels(rows[:1])
        result = import_labels(self.suite, self.packet_dir, self.labels, self.root / "partial")
        self.assertEqual(result["paired_reviewed_cases"], 0)
        self.assertTrue(all(v["paired_correctness"] is None for v in result["variants"].values()))

    def test_no_automatically_created_or_unattested_human_grades(self):
        prepare(self.suite, self.packet_dir)
        original, _ = self.label_rows()
        for field, value in (("correct", None), ("supported", "true"), ("reviewer", ""),
                             ("human_attested", False), ("notes", ""),
                             ("response_hash", "stale"), ("failure_stage", "invented")):
            with self.subTest(field=field):
                self.write_labels([{**original[0], field: value}])
                with self.assertRaises(ValueError):
                    import_labels(self.suite, self.packet_dir, self.labels, self.root / "invalid")
        self.write_labels([original[0], original[0]])
        with self.assertRaises(ValueError):
            import_labels(self.suite, self.packet_dir, self.labels, self.root / "duplicate")

    def test_changed_answer_invalidates_old_packet_even_if_fingerprint_is_unchanged(self):
        prepare(self.suite, self.packet_dir)
        rows, _ = self.label_rows()
        self.write_labels(rows)
        self.change("hybrid", lambda d: d["results"][0].update(answer="Modified cached response"))
        with self.assertRaises(ValueError):
            import_labels(self.suite, self.packet_dir, self.labels, self.root / "stale")

    def test_embedded_json_and_source_html_cannot_inject_scripts(self):
        malicious = '</script><img src="https://evil.invalid" onerror="alert(1)">'
        page = render_review({"rows": [{"answer": malicious}], "sources": {"a": malicious}})
        self.assertNotIn(malicious, page)
        self.assertIn("\\u003c/script\\u003e", page)
        self.assertNotIn("innerHTML", page)

    def test_cli_cannot_publish_raw_review_data_into_frontend(self):
        with (patch("sys.argv", ["quality_review", "--output", "frontend/public/review"]),
              patch("sys.stderr", new_callable=io.StringIO)):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

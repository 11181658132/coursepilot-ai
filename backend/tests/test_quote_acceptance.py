import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.benchmark import DATASET, corpus_hash, validate_dataset
from app.quote_acceptance import VARIANTS, main, selected_case


class QuoteAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        case = next(case for case in validate_dataset() if case.id == "public-30")
        self.report = {"fingerprint": "fixture-experiment", "metadata": {
            "split": "dev", "corpus_sha256": corpus_hash(),
            "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest()},
            "results": [{"id": case.id, "question": case.question, "split": "dev",
                         "course": "DEMO101", "error": None,
                         "diagnostics": {"reason": "quote_not_found"}}]}
        for variant in VARIANTS:
            path = self.root / variant / "latest.json"
            path.parent.mkdir()
            path.write_text(json.dumps({**self.report, "variant": variant}))

    def test_selection_is_exactly_one_verified_fictional_dev_question(self):
        case, sources = selected_case(self.root)
        self.assertEqual(case.id, "public-30")
        self.assertEqual(case.split, "dev")
        self.assertEqual(set(sources), set(VARIANTS))

    def test_changed_question_course_split_reason_and_fingerprint_reject(self):
        target = self.root / "hybrid/latest.json"
        original = json.loads(target.read_text())
        changes = ({"question": "changed"}, {"course": "PRIVATE"}, {"split": "holdout"},
                   {"diagnostics": {"reason": "answered"}})
        for change in changes:
            report = json.loads(json.dumps(original))
            report["results"][0].update(change)
            target.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                selected_case(self.root)
        target.write_text(json.dumps({**original, "fingerprint": "different"}))
        with self.assertRaises(ValueError):
            selected_case(self.root)

    def test_dry_run_never_constructs_sdk_or_creates_output(self):
        output = Path("evals/reports/synthetic-quote-dry-run-test")
        with patch("sys.argv", ["quote_acceptance", "--directory", str(self.root),
                                "--output", str(output)]), patch(
                "app.quote_acceptance.OpenAI") as sdk, patch(
                    "sys.stdout", new_callable=io.StringIO):
            main()
        sdk.assert_not_called()
        self.assertFalse(output.exists())

    def test_rejects_outside_output_and_excess_budget_before_any_provider(self):
        for arguments in (["--output", str(self.root / "outside")],
                          ["--output", "evals/reports/unused", "--budget-usd", "0.06"]):
            with patch("sys.argv", ["quote_acceptance", *arguments]), patch(
                    "app.quote_acceptance.OpenAI") as sdk, patch(
                        "sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    main()
                sdk.assert_not_called()


if __name__ == "__main__":
    unittest.main()

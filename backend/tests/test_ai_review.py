import copy
import unittest

from app.ai_review import analyse
from app.benchmark import VARIANTS


class AIReviewTests(unittest.TestCase):
    def setUp(self):
        self.packet = {"fingerprint": "fixture",
                       "sources": {"fiction.md": "A fictional source sentence."},
                       "rows": [{"review_key": name, "response_hash": name,
                                 "citations": [], "evidence": [{"filename": "fiction.md"}]}
                                for name in VARIANTS]}
        self.mapping = {name: {"variant": name} for name in VARIANTS}
        self.labels = [{"review_key": name, "response_hash": name,
                        "reviewer_kind": "ai_assisted_not_human", "correct": True,
                        "supported": True, "issues": [], "notes": "Synthetic test label.",
                        "source_checks": [{"filename": "fiction.md",
                                           "quote": "A fictional source sentence."}]}
                       for name in VARIANTS]

    def test_provenance_and_denominators_never_become_human_metrics(self):
        self.labels[0]["correct"] = None
        result = analyse(self.packet, self.mapping, self.labels)
        self.assertEqual(result["human_reviewed_outputs"], 0)
        self.assertEqual(result["new_api_calls"], 0)
        values = result["variants"][VARIANTS[0]]
        self.assertEqual(values["unknown_correctness"], 1)
        self.assertEqual(values["correctness"], {"numerator": 0, "denominator": 0, "rate": None})
        self.assertEqual(values["support"]["denominator"], 1)

    def test_rejects_human_attestation_and_wrong_provenance(self):
        for change in ({"human_attested": True}, {"reviewer_kind": "human"}):
            labels = copy.deepcopy(self.labels)
            labels[0].update(change)
            with self.assertRaises(ValueError):
                analyse(self.packet, self.mapping, labels)

    def test_requires_exact_hash_unique_and_complete_coverage(self):
        for labels in (self.labels[:-1], self.labels + [self.labels[0]],
                       [{**self.labels[0], "response_hash": "changed"}, *self.labels[1:]]):
            with self.assertRaises(ValueError):
                analyse(self.packet, self.mapping, labels)

    def test_requires_real_available_source_anchor(self):
        for anchor in ({"filename": "fiction.md", "quote": "An invented supporting sentence."},
                       {"filename": "private.md", "quote": "A fictional source sentence."}):
            labels = copy.deepcopy(self.labels)
            labels[0]["source_checks"] = [anchor]
            with self.assertRaises(ValueError):
                analyse(self.packet, self.mapping, labels)

    def test_rejects_string_grades_missing_notes_unknown_issues(self):
        for change in ({"correct": "true"}, {"notes": ""}, {"issues": ["made_up"]}):
            labels = copy.deepcopy(self.labels)
            labels[0].update(change)
            with self.assertRaises(ValueError):
                analyse(self.packet, self.mapping, labels)

    def test_source_support_and_correctness_are_separate(self):
        self.labels[0].update(supported=False, issues=["unsupported_detail"])
        result = analyse(self.packet, self.mapping, self.labels)["variants"][VARIANTS[0]]
        self.assertEqual(result["correctness"]["rate"], 1)
        self.assertEqual(result["support"]["rate"], 0)
        self.assertEqual(result["both"]["rate"], 0)


if __name__ == "__main__":
    unittest.main()

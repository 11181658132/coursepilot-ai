import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.config import Settings
from app.models import Evidence
from app.services.hybrid_kb import HybridKnowledgeBase, ModelOutputError, validate_answer


class CitationDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [Evidence(filename="source.md", chunk_id="chunk-a", source_id="S1",
                                  text="Original evidence supports this specific statement.")]
        self.draft = {"answer": "Grounded statement. [S1]", "citations": [
            {"source_id": "S1", "quote": "Original evidence supports this specific statement."}]}

    def reason(self):
        answer, citations, diagnostic = validate_answer(self.draft, self.evidence)
        if diagnostic.reason != "answered":
            self.assertIsNone(answer)
            self.assertEqual(citations, [])
        return diagnostic.reason

    def test_grounded_answer_and_real_quote_are_preserved(self):
        self.assertEqual(self.reason(), "answered")
        answer, _, diagnostic = validate_answer(self.draft, self.evidence)
        self.assertEqual(answer, self.draft["answer"])
        self.assertFalse(diagnostic.citation_markers_repaired)

    def test_absent_markers_are_added_only_for_verified_structured_citations(self):
        self.draft["answer"] = "Grounded statement."
        answer, citations, diagnostic = validate_answer(self.draft, self.evidence)
        self.assertEqual(answer, "Grounded statement. [S1]")
        self.assertEqual(len(citations), 1)
        self.assertTrue(diagnostic.citation_markers_repaired)
        self.assertEqual(self.draft["answer"], "Grounded statement.")

    def test_multiple_markers_preserve_citation_order_and_deduplicate(self):
        self.evidence.append(self.evidence[0].model_copy(update={"source_id": "S2"}))
        self.draft["answer"] = "Grounded statement."
        self.draft["citations"].insert(0, dict(self.draft["citations"][0], source_id="S2"))
        self.draft["citations"].append(self.draft["citations"][0].copy())
        answer, citations, diagnostic = validate_answer(self.draft, self.evidence)
        self.assertEqual(answer, "Grounded statement. [S2] [S1]")
        self.assertEqual(len(citations), 2)
        self.assertTrue(diagnostic.citation_markers_repaired)

    def test_malformed_existing_marker_is_not_silently_repaired(self):
        for marker in ("[S1,S2]", "[S99]", "[s1]", "[ S1 ]"):
            self.draft["answer"] = "Statement. " + marker
            self.assertEqual(self.reason(), "inline_citation_mismatch")

    def test_missing_markers_do_not_bypass_quote_validation(self):
        self.draft["answer"] = "Grounded statement."
        self.draft["citations"][0]["quote"] = "Invented unsupported quotation"
        self.assertEqual(self.reason(), "quote_not_found")

    def test_unknown_source_is_never_repaired_by_guessing_an_existing_source(self):
        self.draft["citations"][0]["source_id"] = "S99"
        self.assertEqual(self.reason(), "unknown_source_id")

    def test_invented_or_short_quote_is_reported_separately(self):
        self.draft["citations"][0]["quote"] = "A fabricated and unsupported statement"
        self.assertEqual(self.reason(), "quote_not_found")
        self.draft["citations"][0]["quote"] = "text"
        self.assertEqual(self.reason(), "quote_too_short")

    def test_whitespace_wrapping_is_allowed_but_words_cannot_change(self):
        self.evidence[0].text = "Original evidence\n supports this specific statement."
        self.assertEqual(self.reason(), "answered")
        self.draft["citations"][0]["quote"] = "Original evidence supports another statement."
        self.assertEqual(self.reason(), "quote_not_found")

    def test_no_citations_does_not_claim_to_know_model_intent(self):
        self.draft = {"answer": "Insufficient evidence.", "citations": []}
        self.assertEqual(self.reason(), "generation_no_citations")
        self.draft["answer"] = "Uncited factual answer."
        self.assertEqual(self.reason(), "generation_no_citations")

    def test_mismatched_inline_ids_are_not_shown(self):
        self.draft["answer"] = "Some answer. [S2]"
        self.assertEqual(self.reason(), "inline_citation_mismatch")

    def test_single_bad_citation_rejects_whole_draft(self):
        self.draft["citations"].append({"source_id": "S2", "quote": "Invented source text"})
        self.assertEqual(self.reason(), "unknown_source_id")

    def test_malformed_json_object_cannot_reach_published_answer(self):
        for draft in ([], {"answer": "missing citations"}, {"answer": None, "citations": []},
                      {"answer": "x", "citations": ["not an object"]}):
            self.draft = draft
            self.assertEqual(self.reason(), "invalid_model_output")


class StructuredOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = Settings(_env_file=None, rag_db_path=root / "index.sqlite3",
                                 telemetry_path=root / "events.jsonl")
        self.valid = {"answer": "Verified answer. [S1]", "citations": [
            {"source_id": "S1", "quote": "This exact original sentence is supported."}]}

    def tearDown(self):
        self.temporary.cleanup()

    def base(self, status="completed", output=None, text=None):
        response = SimpleNamespace(status=status, output=output or [],
                                   output_text=json.dumps(self.valid) if text is None else text,
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=20))
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        return HybridKnowledgeBase(self.settings, client)

    def test_incomplete_json_is_distinct_from_insufficient_knowledge(self):
        base = self.base(status="incomplete")
        with self.assertRaises(ModelOutputError) as raised:
            base._json_response("grounded_answer", {}, "instructions", {})
        self.assertEqual(raised.exception.reason, "response_incomplete")
        self.assertEqual(base.model_usage["output_tokens"], 20)

    def test_refusal_content_is_handled_before_json_parsing(self):
        output = [SimpleNamespace(content=[SimpleNamespace(type="refusal",
                                                           refusal="private body")])]
        base = self.base(output=output, text="")
        with self.assertRaises(ModelOutputError) as raised:
            base._json_response("grounded_answer", {}, "instructions", {})
        self.assertEqual(str(raised.exception), "provider_refusal")
        self.assertNotIn("private", str(raised.exception))

    def test_invalid_json_uses_safe_reason_code(self):
        base = self.base(text="private unparseable provider response")
        with self.assertRaises(ModelOutputError) as raised:
            base._json_response("grounded_answer", {}, "instructions", {})
        self.assertEqual(str(raised.exception), "invalid_model_output")

    def test_chat_keeps_unvalidated_answer_out_of_response_and_telemetry(self):
        self.valid["citations"][0]["quote"] = "Invented SECRET phrase"
        base = self.base()
        row = {"filename": "source.md", "chunk_id": "a", "text":
               "This exact original sentence is supported.", "score": 1}
        with patch.object(base, "retrieve", return_value=[row]):
            response = base.chat("DEMO101", "all", "question")
        self.assertEqual(response.diagnostics.reason, "quote_not_found")
        self.assertEqual(response.citations, [])
        self.assertNotIn("SECRET", response.answer)
        self.assertNotIn("资料中没有足够证据", response.answer)
        log = self.settings.telemetry_path.read_text()
        self.assertIn("quote_not_found", log)
        self.assertNotIn("SECRET", log)
        self.assertNotIn("source.md", log)

    def test_chat_returns_verified_markers_and_records_only_safe_diagnostics(self):
        self.valid["answer"] = "Verified answer."
        base = self.base()
        row = {"filename": "source.md", "chunk_id": "a", "text":
               "This exact original sentence is supported.", "score": 1}
        with patch.object(base, "retrieve", return_value=[row]):
            response = base.chat("DEMO101", "all", "question")
        self.assertEqual(response.answer, "Verified answer. [S1]")
        self.assertTrue(response.diagnostics.citation_markers_repaired)
        self.assertEqual(response.diagnostics.reason, "answered")
        self.assertEqual(len(response.citations), 1)
        log = self.settings.telemetry_path.read_text()
        self.assertIn('"citation_markers_repaired": true', log)
        self.assertNotIn("Verified answer", log)


if __name__ == "__main__":
    unittest.main()

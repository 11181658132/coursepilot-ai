import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.config import PROJECT_ROOT, Settings
from app.models import Evidence
from app.services.hybrid_kb import (
    HybridKnowledgeBase,
    quote_catalog,
    resolve_quote_ids,
    validate_answer,
)


class QuoteCatalogTests(unittest.TestCase):
    def setUp(self):
        self.text = "Use per-user limits, monthly request caps, timeouts, and a dedicated project."
        self.evidence = [Evidence(source_id="S1", chunk_id="chunk-1", filename="fiction.md",
                                  text=self.text, page=2, document_version="fiction-version")]
        self.catalog = quote_catalog(self.evidence)
        self.draft = {"answer": "Use request caps and timeouts. [S1]", "citations": [
            {"source_id": "S1", "quote_id": "S1.Q1"}]}

    def test_selected_id_resolves_exact_original_with_location_and_version(self):
        before = copy.deepcopy(self.draft)
        answer, citations, diagnostics = resolve_quote_ids(
            self.draft, self.evidence, self.catalog)
        self.assertEqual(answer, self.draft["answer"])
        self.assertEqual(citations[0].quote, self.text)
        self.assertEqual(citations[0].page, 2)
        self.assertEqual(citations[0].document_version, "fiction-version")
        self.assertEqual(diagnostics.reason, "answered")
        self.assertEqual(self.draft, before)

    def test_paraphrased_quote_still_rejected_id_selection_avoids_retyping(self):
        # Synthetic mechanism regression, NOT replay of an unavailable historical draft.
        paraphrase = {"answer": self.draft["answer"], "citations": [{"source_id": "S1",
            "quote": "Use user limits, monthly budgets, timeouts and a dedicated project."}]}
        self.assertEqual(validate_answer(paraphrase, self.evidence)[2].reason, "quote_not_found")
        self.assertEqual(resolve_quote_ids(paraphrase, self.evidence, self.catalog)[2].reason,
                         "quote_not_found")
        self.assertEqual(resolve_quote_ids(self.draft, self.evidence, self.catalog)[2].reason,
                         "answered")

    def test_unknown_or_cross_source_id_cannot_pick_a_different_quote(self):
        other = Evidence(source_id="S2", filename="other.md",
                         text="Different original evidence in another source.")
        evidence = [*self.evidence, other]
        catalog = quote_catalog(evidence)
        for identifier in ("S99.Q1", "S1.Q999", "S2.Q1"):
            self.draft["citations"][0]["quote_id"] = identifier
            result = resolve_quote_ids(self.draft, evidence, catalog)
            self.assertIsNone(result[0])
            self.assertEqual(result[1], [])
            self.assertEqual(result[2].reason, "unknown_source_id")

    def test_mixed_protocols_extra_fields_and_wrong_types_reject_entire_draft(self):
        for item in ({"source_id": "S1", "quote_id": 1},
                     {"source_id": "S1", "quote_id": "S1.Q1", "quote": self.text},
                     {"source_id": None, "quote_id": "S1.Q1"}):
            draft = {**self.draft, "citations": [item]}
            self.assertEqual(resolve_quote_ids(draft, self.evidence, self.catalog)[2].reason,
                             "invalid_model_output")
        self.draft["citations"].append({"source_id": "S1", "quote": self.text})
        self.assertEqual(resolve_quote_ids(self.draft, self.evidence, self.catalog)[2].reason,
                         "invalid_model_output")

    def test_catalog_text_and_inline_ids_are_independently_validated(self):
        self.catalog["S1.Q1"]["quote"] = "Invented quote must never be published."
        self.assertEqual(resolve_quote_ids(self.draft, self.evidence, self.catalog)[2].reason,
                         "quote_not_found")
        self.catalog = quote_catalog(self.evidence)
        self.draft["answer"] = "Statement. [S99]"
        self.assertEqual(resolve_quote_ids(self.draft, self.evidence, self.catalog)[2].reason,
                         "inline_citation_mismatch")

    def test_source_change_after_menu_creation_invalidates_old_quote(self):
        changed = [self.evidence[0].model_copy(update={"text": "A replacement source passage."})]
        self.assertEqual(resolve_quote_ids(self.draft, changed, self.catalog)[2].reason,
                         "quote_not_found")

    def test_catalog_is_deterministic_bounded_and_never_rewrites_source_words(self):
        for text in ("A sentence with\n wrapped whitespace.\n\n中文原文保持标点与字符。",
                     "x" * 1600, "Long source sentence. " * 2000, "A. B. C."):
            source = Evidence(source_id="S1", filename="fiction.md", text=text)
            catalog = quote_catalog([source])
            self.assertEqual(catalog, quote_catalog([source]))
            self.assertLessEqual(len(catalog), 64)
            self.assertTrue(catalog)
            for item in catalog.values():
                self.assertTrue(8 <= len(item["quote"]) <= 250)
                self.assertIn(item["quote"], text)

    def test_fictional_cost_controls_have_an_exact_selectable_sentence(self):
        # Existing versioned public source, no private courses or held-out questions.
        path = PROJECT_ROOT / "frontend/demo-knowledge/tutorial-02-cost-and-observability.md"
        source = Evidence(source_id="S1", filename=path.name, text=path.read_text())
        catalog = quote_catalog([source])
        matches = [(key, value) for key, value in catalog.items()
                   if value["quote"].startswith("Useful controls include")]
        self.assertEqual(len(matches), 1)
        key, item = matches[0]
        self.assertIn("dedicated API project", item["quote"])
        draft = {"answer": "Controls are listed in the course. [S1]", "citations": [
            {"source_id": "S1", "quote_id": key}]}
        self.assertEqual(resolve_quote_ids(draft, [source], catalog)[2].reason, "answered")

    def test_large_candidate_set_stays_within_schema_enum_budget(self):
        text = " ".join(f"Distinct fictional sentence number {n}." for n in range(100))
        evidence = [Evidence(source_id=f"S{i}", filename="fiction.md", text=text)
                    for i in range(1, 25)]
        catalog = quote_catalog(evidence)
        self.assertLessEqual(len(catalog), 800)
        self.assertEqual({value["source_id"] for value in catalog.values()},
                         {source.source_id for source in evidence})

    def test_uncited_refusal_is_not_converted_to_a_supported_answer(self):
        draft = {"answer": "Evidence is insufficient.", "citations": []}
        self.assertEqual(resolve_quote_ids(draft, self.evidence, self.catalog)[2].reason,
                         "generation_no_citations")

    def test_chat_uses_enum_ids_without_extra_paid_retry_or_quote_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Settings(_env_file=None, rag_db_path=Path(directory) / "index.sqlite3",
                              telemetry_path=Path(directory) / "events.jsonl")
            base = HybridKnowledgeBase(config)
            row = {"filename": "fiction.md", "chunk_id": "chunk-1", "text": self.text,
                   "score": 1}

            def respond(name, schema, instructions, payload):
                self.assertEqual(name, "grounded_answer")
                props = schema["properties"]["citations"]["items"]["properties"]
                self.assertNotIn("quote", props)
                option = payload["passages"][0]["quote_options"][0]
                self.assertIn(option["quote_id"], props["quote_id"]["enum"])
                self.assertEqual(option["quote"], self.text)
                return self.draft

            with patch.object(base, "retrieve", return_value=[row]), patch.object(
                    base, "_current_rows", return_value=[row]), patch.object(
                    base, "_json_response", side_effect=respond) as model:
                result = base.chat("DEMO101", "all", "What controls are listed?")
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result.diagnostics.reason, "answered")
            self.assertEqual(result.citations[0].quote, self.text)
            event = json.loads(config.telemetry_path.read_text())
            self.assertNotIn(self.text, json.dumps(event))
            self.assertNotIn("quote_id", event)

    def test_chat_does_not_retry_a_rejected_selected_id(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Settings(_env_file=None, rag_db_path=Path(directory) / "index.sqlite3",
                              telemetry_path=Path(directory) / "events.jsonl")
            base = HybridKnowledgeBase(config)
            row = {"filename": "fiction.md", "chunk_id": "chunk-1", "text": self.text,
                   "score": 1}
            self.draft["citations"][0]["quote_id"] = "S1.Q999"
            model = Mock(return_value=self.draft)
            with patch.object(base, "retrieve", return_value=[row]), patch.object(
                    base, "_current_rows", return_value=[row]), patch.object(
                    base, "_json_response", model):
                result = base.chat("DEMO101", "all", "What controls are listed?")
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result.citations, [])
            self.assertEqual(result.diagnostics.reason, "unknown_source_id")


if __name__ == "__main__":
    unittest.main()

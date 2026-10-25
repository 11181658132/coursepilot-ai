import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.challenge_data import VARIANTS, load_dataset, materialize, passage_metrics
from app.challenge_review import analyse
from app.challenge_suite import (
    CampaignSDK,
    averages,
    digest,
    load_tasks,
    main,
    run_agents,
    run_rag,
)
from app.config import Settings
from app.eval_budget import BudgetExceeded, BudgetLedger
from app.services.retrieval import rank_candidates


class Tool(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return vars(self)


class FakeSDK:
    """Scripted transport fixture; no network-capable fallback and no semantic gold."""
    def __init__(self):
        self.responses = SimpleNamespace(create=self.respond)
        self.embeddings = SimpleNamespace(create=self.embed)
        self.calls = 0
        self.embedding_inputs = []

    def embed(self, **kwargs):
        self.embedding_inputs.extend(kwargs["input"])
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=50), data=[
            SimpleNamespace(index=i, embedding=[1., 1.]) for i, _ in enumerate(kwargs["input"])])

    def respond(self, **kwargs):
        self.calls += 1
        history = kwargs["input"]
        if isinstance(history, str):
            payload = json.loads(history)
            if kwargs["text"]["format"]["name"] == "relevance_scores":
                result = {"scores": [{"chunk_id": p["chunk_id"], "score": 3}
                                     for p in payload["passages"]]}
            else:
                source = payload["passages"][0]
                result = {"answer": "Scripted response, not a correct-answer label. [S1]",
                          "citations": [{"source_id": "S1",
                                         "quote_id": source["quote_options"][0]["quote_id"]}]}
            return SimpleNamespace(status="completed", output=[], output_text=json.dumps(result),
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=100))
        body = json.loads(history[0]["content"])
        forced = kwargs["tool_choice"]
        if isinstance(forced, dict):
            name = forced["name"]
        else:
            called = {item.get("name") for item in history}
            name = ("list_materials" if "list_materials" not in called else "search_knowledge"
                    if "search_knowledge" not in called else "propose_study_plan")
        args = {}
        if name == "search_knowledge":
            args = {"query": "keyword semantic retrieval meaning lexical"}
        elif name in {"propose_study_plan", "propose_feedback"}:
            source = json.loads(history[-1]["output"])["passages"][0]
            quote = source["text"].split(". ")[0]
            if not quote.endswith("."):
                quote += "."
            citation = {"source_id": source["source_id"], "quote": quote}
            if name == "propose_study_plan":
                args = {"title": "Scripted fixture plan", "tasks": [{
                    "day": day, "title": "Review source", "study_goal": "Read course evidence",
                    "quiz_question": "What does the source state?", "quiz_answer": quote,
                    "citation": citation} for day in range(1, body["days"] + 1)]}
            else:
                question = body["quiz"]["quiz_question"]
                verdict = ("uncertain" if "office" in question else "mostly_correct"
                           if "first is keyword" in body["student_answer"] else "needs_review")
                args = {"assessment": {"verdict": verdict, "explanation": "Check the source.",
                    "weaknesses": ["Lexical versus semantic confusion"] if verdict == "needs_review"
                    else [], "citations": [] if verdict == "uncertain" else [citation]},
                    "adjustments": []}
                if verdict == "needs_review" and body["remaining_tasks"]:
                    args["adjustments"] = [{"task_id": body["remaining_tasks"][0]["id"],
                        "title": "Review distinction", "study_goal": "Study the source distinction",
                        "quiz_question": "What is the distinction?", "quiz_answer": quote,
                        "citation": citation}]
        return SimpleNamespace(status="completed", output=[Tool(type="function_call", name=name,
                               arguments=json.dumps(args), call_id="fixture-call")], output_text="",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=100))


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Settings(_env_file=None, knowledge_base_dir=self.root / "knowledge",
            rag_db_path=self.root / "index.sqlite3", telemetry_path=self.root / "telemetry.jsonl",
            max_search_results=3, rag_candidate_count=10, rag_rerank=False,
            agent_run_budget_usd=.05, agent_daily_budget_usd=.5)
        self.data, self.passages = load_dataset()
        self.sdk = FakeSDK()
        self.client = CampaignSDK(self.sdk, BudgetLedger(self.root / "budget.json", .5))

    def test_frozen_counts_scope_and_split(self):
        self.assertEqual(len(self.passages), 40)
        self.assertEqual(sum(c["split"] == "dev" for c in self.data["cases"]), 24)
        self.assertEqual(sum(c["split"] == "holdout" for c in self.data["cases"]), 8)
        self.assertEqual(len(load_tasks()), 6)

    def test_passage_metrics_do_not_substitute_same_file_for_correct_passage(self):
        case = next(c for c in self.data["cases"] if c["id"] == "current-context")
        wrong = passage_metrics(case, ["orion-current:threshold", "orion-archive:context"],
                                self.passages)
        self.assertEqual(wrong["recall_at_3"], 0)
        self.assertEqual(wrong["archived_negative_hits"], 1)
        correct = passage_metrics(case, ["orion-current:context"], self.passages)
        self.assertEqual(correct["recall_at_3"], 1)
        self.assertEqual(correct["precision_at_3"], 1/3)
        self.assertEqual(correct["ndcg_at_3"], 1)

    def test_empty_and_absent_gold_denominators_are_explicit(self):
        case = next(c for c in self.data["cases"] if c["id"] == "missing-office")
        result = passage_metrics(case, ["other-office:office"], self.passages)
        self.assertIsNone(result["recall_at_3"])
        self.assertIsNone(result["ndcg_at_3"])
        self.assertEqual(result["cross_course_count"], 1)
        self.assertIsNone(averages([])["recall_at_3"]["mean"])

    def test_ablation_modes_differ_and_default_hybrid_is_preserved(self):
        rows = [{"chunk_id": "lexical", "text": "exact-keyword", "embedding": [0., 1.]},
                {"chunk_id": "semantic", "text": "different wording", "embedding": [1., 0.]}]
        self.assertEqual(rank_candidates("exact-keyword", rows, [], 2, "bm25")[0]["chunk_id"],
                         "lexical")
        dense = rank_candidates("exact-keyword", rows, [1., 0.], 2, "vector")
        self.assertEqual(dense[0]["chunk_id"], "semantic")
        self.assertEqual(rank_candidates("exact-keyword", rows, [1., 0.], 2),
                         rank_candidates("exact-keyword", rows, [1., 0.], 2, "hybrid"))
        with self.assertRaises(ValueError):
            rank_candidates("query", rows, [], 2, "invented")

    def test_invalid_tools_and_models_are_rejected_before_charge(self):
        for payload in ({"model": "other-model"}, {"model": "gpt-6-luna", "tools": [
                {"type": "function", "name": "shell"}]}):
            with self.assertRaises(ValueError):
                self.client.respond(**payload)
        self.assertEqual(self.client.ledger.data["calls"], 0)
        self.assertEqual(self.sdk.calls, 0)

    def test_uncertain_call_retains_reserve_and_stops_later_calls(self):
        self.sdk.responses.create = lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):
            self.client.respond(model="gpt-6-luna", input="fixture")
        self.assertTrue(self.client.failed)
        self.assertEqual(self.client.ledger.data["uncertain_calls"], 1)
        with self.assertRaises(BudgetExceeded):
            self.client.respond(model="gpt-6-luna", input="fixture")
        self.assertEqual(self.client.ledger.data["calls"], 1)

    def test_full_scripted_campaign_excludes_holdout_and_catches_state_faults(self):
        materialize(self.config, self.data, self.passages, self.client, self.root)
        report = {"records": {}}
        with patch("sys.stdout", new_callable=io.StringIO):
            run_rag(self.config, self.client, self.data, self.passages,
                    self.root, report, lambda: None)
            run_agents(self.config, self.client, self.passages, self.root, report, lambda: None)
        rag = [r for r in report["records"].values() if r["kind"] == "rag"]
        self.assertEqual(len(rag), 96)
        self.assertEqual({r["variant"] for r in rag}, set(VARIANTS))
        self.assertFalse(any(r["metrics"]["cross_course_count"] for r in rag))
        self.assertTrue(all(r["case_id"] in {c["id"] for c in self.data["cases"]
                                            if c["split"] == "dev"} for r in rag))
        for name in ("progress-conflict", "retrieval-failure"):
            controls = report["records"][f"agent:{name}"]["controls"]
            self.assertTrue(all(controls.values()), controls)
        plan = report["records"]["agent:goal-coverage"]
        self.assertTrue(plan["controls"]["delivered_plan"], plan)
        self.assertNotIn("semantic_accuracy", plan)
        calls = self.client.ledger.data["calls"]
        run_agents(self.config, self.client, self.passages, self.root, report, lambda: None)
        self.assertEqual(calls, self.client.ledger.data["calls"])

    def test_document_vectors_and_bm25_receive_identical_passage_text(self):
        base = materialize(self.config, self.data, self.passages, self.client, self.root)
        rows = base.store.rows(base.signature, "LAB202", "all")
        rows += base.store.rows(base.signature, "OTHER303", "all")
        self.assertEqual(set(self.sdk.embedding_inputs), {r["text"] for r in rows})
        self.assertEqual(len(self.sdk.embedding_inputs), 40)

    def test_run_cannot_put_a_fresh_ledger_beside_an_unrelated_output(self):
        with patch("sys.argv", ["challenge_suite", "--live", "--output", str(self.root),
                                "--budget-root", str(self.root / "different")]), patch(
                "app.challenge_suite.OpenAI") as sdk, patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                main()
        sdk.assert_not_called()

    def test_free_cli_never_constructs_sdk(self):
        with patch("sys.argv", ["challenge_suite"]), patch(
                "app.challenge_suite.OpenAI") as sdk, patch("sys.stdout", new_callable=io.StringIO):
            main()
        sdk.assert_not_called()


class ChallengeReviewTests(unittest.TestCase):
    def setUp(self):
        self.record = {"kind": "rag", "variant": "bm25",
                       "review_dimensions": ["correct", "supported"]}
        self.report = {"fingerprint": "fixture", "records": {"key": self.record}}
        self.label = {"key": "key", "record_sha256": digest(self.record),
                      "provenance": "ai_assisted_not_human", "scores": {
                          "correct": None, "supported": False}, "notes": "Synthetic fixture label."}

    def test_unknown_is_not_pass_and_human_coverage_remains_zero(self):
        result = analyse(self.report, [self.label])
        self.assertEqual(result["human_reviews"], 0)
        scores = result["groups"]["rag:bm25"]
        self.assertIsNone(scores["dimensions"]["correct"]["rate"])
        self.assertEqual(scores["dimensions"]["supported"]["rate"], 0)
        self.assertEqual(scores["unknown_values"], 1)

    def test_hash_change_missing_duplicate_and_human_fields_reject(self):
        for change in ({"record_sha256": "changed"}, {"human_attested": True},
                       {"scores": {"correct": "true", "supported": True}}, {"notes": ""}):
            label = copy.deepcopy(self.label)
            label.update(change)
            with self.assertRaises(ValueError):
                analyse(self.report, [label])
        for labels in ([], [self.label, self.label]):
            with self.assertRaises(ValueError):
                analyse(self.report, labels)


if __name__ == "__main__":
    unittest.main()

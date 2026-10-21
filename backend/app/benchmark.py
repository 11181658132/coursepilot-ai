"""Three-engine evaluation against ONLY the versioned fictional DEMO101 corpus.

No remote writes or private course indexing. Existing hosted files are hash-verified.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values
from openai import NotFoundError, OpenAI

from app.config import PROJECT_ROOT, get_settings
from app.eval_budget import BudgetClient, BudgetExceeded, BudgetLedger
from app.evaluation import (
    EvalResult,
    apply_reviews,
    load_cases,
    run_evaluation,
    summarise,
    write_report,
    write_review_template,
)
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.openai_kb import OpenAIKnowledgeBase
from app.services.state import save_state

DATASET = PROJECT_ROOT / "evals/public_questions.jsonl"
SOURCES = PROJECT_ROOT / "frontend/demo-knowledge"
VARIANTS = ("file_search", "hybrid-no-rerank", "hybrid")


def validate_dataset():
    cases = load_cases(DATASET)
    groups = {}
    for case in cases:
        if case.course != "DEMO101" or case.split not in {"dev", "holdout"}:
            raise ValueError("Public benchmark must contain only explicit DEMO101 splits")
        if case.group in groups and groups[case.group] != case.split:
            raise ValueError("A question family crosses dev/holdout splits")
        groups[case.group] = case.split
        if not case.reference_answer or case.label_status != "draft_source_checked":
            raise ValueError("Cases need an explicitly labelled draft reference")
        for filename in case.expected_files:
            if Path(filename).name != filename or not (SOURCES / filename).is_file():
                raise ValueError("Unversioned expected source")
        for quote in case.source_quotes:
            filename = quote["filename"]
            if Path(filename).name != filename or not (SOURCES / filename).is_file():
                raise ValueError("Unversioned reference quote source")
            if not quote["text"] or quote["text"] not in (SOURCES / filename).read_text():
                raise ValueError("Reference quote not present in the fictional source")
    if len(cases) != 50 or Counter(case.split for case in cases) != {"dev": 30, "holdout": 20}:
        raise ValueError("Expected 30 development and 20 held-out draft cases")
    return cases


def corpus_hash():
    return hashlib.sha256(json.dumps({source.name: hashlib.sha256(source.read_bytes()).hexdigest()
                                     for source in sorted(SOURCES.glob("*.md"))},
                                    sort_keys=True).encode()).hexdigest()


def verify_hosted_corpus(client, store_id):
    files = list(client.vector_stores.files.list(vector_store_id=store_id, limit=100))
    if len(files) != 5:
        raise ValueError("Existing hosted corpus is not the five-file public corpus")
    seen = set()
    for item in files:
        if item.status != "completed" or item.attributes.get("course") != "DEMO101":
            raise ValueError("Hosted files must be completed fictional DEMO101 sources")
        metadata = client.files.retrieve(item.id)
        filename = metadata.filename
        if Path(filename).name != filename or not (SOURCES / filename).is_file():
            raise ValueError("Hosted corpus contains an unknown file")
        # assistants-purpose originals cannot be downloaded via Files.content.
        # Compare parsed text through the official vector-store content endpoint,
        # normalizing whitespace only, not punctuation, casing, or words.
        contents = client.vector_stores.files.content(item.id, vector_store_id=store_id)
        actual = "".join("".join(part.text.split()) for part in contents if part.type == "text")
        expected = "".join((SOURCES / filename).read_text().split())
        if hashlib.sha256(actual.encode()).digest() != hashlib.sha256(expected.encode()).digest():
            raise ValueError("Hosted public source hash differs from local source")
        expected_type = "tutorial" if filename.startswith("tutorial") else "lecture"
        if item.attributes.get("material_type") != expected_type:
            raise ValueError("Hosted source has incorrect material attributes")
        seen.add(filename)
    if seen != {path.name for path in SOURCES.glob("*.md")}:
        raise ValueError("Hosted corpus has duplicate/missing fictional sources")


def resolve_public_store(client, configured_id):
    try:
        verify_hosted_corpus(client, configured_id)
        return configured_id
    except NotFoundError:
        # Config can refer to an expired store or a different OpenAI project.
        # Read only one exact-name existing candidate, then verify every source hash.
        candidates = [store for store in client.vector_stores.list(limit=100)
                      if store.name == "CoursePilot Public Demo"]
        if len(candidates) != 1:
            raise ValueError("No unambiguous existing public store; user action needed") from None
        verify_hosted_corpus(client, candidates[0].id)
        return candidates[0].id


class DemoHostedBase(OpenAIKnowledgeBase):
    def __init__(self, config, client, store_id, state_path):
        save_state(state_path, {"stores": {f"DEMO101:{kind}": {"id": store_id}
                                           for kind in ("lecture", "tutorial", "other")}})
        super().__init__(api_key=config.openai_api_key, model=config.openai_model,
                         state_path=state_path, max_results=4)
        self.budget_client = client

    def chat(self, course, material_type, question):
        if course != "DEMO101":
            raise ValueError("Private courses are forbidden in public comparisons")

        def scoped(**kwargs):
            filters = [{"type": "eq", "key": "course", "value": "DEMO101"}]
            if material_type != "all":
                filters.append({"type": "eq", "key": "material_type", "value": material_type})
            kwargs["tools"][0]["filters"] = (filters[0] if len(filters) == 1
                                                else {"type": "and", "filters": filters})
            kwargs["tool_choice"] = "required"
            return self.budget_client.responses.create(**kwargs)

        self.client = SimpleNamespace(responses=SimpleNamespace(create=scoped))
        return super().chat(course, material_type, question)


def prepare_hybrid(config, client, directory):
    index = json.loads((PROJECT_ROOT / "frontend/demo-index.json").read_text())
    knowledge = directory / "knowledge"
    for document in index["documents"]:
        relative = Path(document["path"])
        if relative.parts[0] != "DEMO101" or ".." in relative.parts:
            raise ValueError("Only fictional relative paths may be rehydrated")
        content = (SOURCES / relative.name).read_bytes()
        if hashlib.sha256(content).hexdigest() != document["sha256"]:
            raise ValueError("Precomputed demo index is stale")
        target = knowledge / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    config = config.model_copy(update={
        "knowledge_base_dir": knowledge, "rag_db_path": directory / "index.sqlite3",
        "rag_embedding_model": index["embedding_model"], "rag_chunk_chars": index["chunk_chars"],
        "rag_chunk_overlap": index["chunk_overlap"], "max_search_results": 4,
        "telemetry_path": directory / "telemetry.jsonl",
    })
    base = HybridKnowledgeBase(config, client)
    for document in index["documents"]:
        chunks = [row for row in index["chunks"] if row["path"] == document["path"]]
        base.store.replace(SimpleNamespace(relative_path=document["path"], course="DEMO101",
                                           material_type=document["material_type"],
                                           sha256=document["sha256"]), base.signature,
                           [{key: value for key, value in row.items() if key != "embedding"}
                            for row in chunks], [row["embedding"] for row in chunks])
    return base


def compare(directory: Path):
    reports = {variant: json.loads((directory / variant / "latest.json").read_text())
               for variant in VARIANTS if (directory / variant / "latest.json").is_file()}
    if len(reports) != 3:
        raise ValueError("Three variant reports are required")
    if len({report["fingerprint"] for report in reports.values()}) != 1:
        raise ValueError("Cannot compare different datasets/corpora/configurations")
    coverage = {name: {row["id"] for row in report["results"] if not row["error"]}
                for name, report in reports.items()}
    common = set.intersection(*coverage.values())
    comparison = {"fingerprint": reports["hybrid"]["fingerprint"],
                  "paired_case_count": len(common), "warning": "Draft labels; proxy metrics only.",
                  "variants": {name: {
                      "all": report["summary"],
                      "paired": summarise(EvalResult(**row) for row in report["results"]
                                          if row["id"] in common),
                      "human_review": report.get("human_review", {"reviewed": 0}),
                  } for name, report in reports.items()}}
    save_state(directory / "comparison.json", comparison)
    lines = ["# RAG comparison (draft labels; automated proxies)", "",
             f"Paired completed cases: {len(common)}", "",
             "| Variant | Proxy pass | Retrieval hit | Citation precision | Keywords "
             "| P50 ms | P95 ms | Cost USD |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, data in comparison["variants"].items():
        summary = data["paired"]
        lines.append("| " + " | ".join(str(value) for value in [name, summary["pass_rate"],
                     summary["retrieval_hit_rate"], summary["citation_precision"],
                     summary["keyword_recall"], summary["p50_latency_ms"],
                     summary["p95_latency_ms"], data["all"]["estimated_cost_usd"]]) + " |")
    (directory / "comparison.md").write_text("\n".join(lines) + "\n")
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--live", action="store_true", help="Explicit paid opt-in")
    parser.add_argument("--split", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--budget-usd", type=float, default=0.50)
    parser.add_argument("--output-dir", type=Path,
                        default=PROJECT_ROOT / "evals/reports/public-benchmark")
    parser.add_argument("--review-template", type=Path, help="Existing variant latest.json")
    parser.add_argument("--apply-reviews", type=Path, help="Review JSONL; requires --report")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cases = validate_dataset()
    if args.review_template:
        report = json.loads(args.review_template.read_text())
        write_review_template(report, args.review_template.with_name("review.jsonl"))
        return
    if args.apply_reviews:
        if args.report is None:
            parser.error("--report is required")
        report = json.loads(args.report.read_text())
        report["human_review"] = apply_reviews(report, args.apply_reviews)
        write_report(report, args.report.parent)
        return
    if args.split == "holdout" and not args.allow_holdout:
        parser.error("Holdout is frozen; explicitly pass --allow-holdout for release evaluation")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if not args.live:
        print(json.dumps({"validated_cases": len(cases),
                          "splits": dict(Counter(c.split for c in cases)),
                          "label_status": "draft_source_checked", "api_calls": 0}))
        return
    run_live(args, [case for case in cases if case.split == args.split][:args.limit])


def run_live(args, cases):
    config = get_settings().model_copy(update={"openai_max_retries": 0})
    if config.openai_model != "gpt-6-luna":
        raise ValueError("Keep the approved gpt-6-luna configuration")
    directory = args.output_dir
    directory.mkdir(parents=True, exist_ok=True)
    # A single ledger is shared across dev/holdout and resume attempts.
    ledger = BudgetLedger(directory / "budget.json", args.budget_usd)
    with ledger.lock():
        sdk = OpenAI(api_key=config.openai_api_key, max_retries=0, timeout=45)
        values = dotenv_values(PROJECT_ROOT / ".env")
        state = json.loads((PROJECT_ROOT / "backend/data/demo_store.json").read_text())
        store_id = values.get("OPENAI_DEMO_VECTOR_STORE_ID") or state["vector_store_id"]
        store_id = resolve_public_store(sdk, store_id)
        if sum(path.stat().st_size for path in SOURCES.glob("*.md")) > 10_000:
            raise ValueError("Public corpus exceeds the cost-reservation context bound")
        client = BudgetClient(sdk, ledger)
        hybrid = prepare_hybrid(config, client, directory / "runtime")
        hosted = DemoHostedBase(config, client, store_id, directory / "hosted-state.json")
        metadata = {"dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                    "implementation_sha256": hashlib.sha256(b"".join(
                        path.read_bytes() for path in sorted((PROJECT_ROOT / "backend/app").rglob(
                            "*.py")))).hexdigest(),
                    "corpus_sha256": corpus_hash(), "scoring_version": 2,
                    "model": config.openai_model, "max_results": 4,
                    "embedding_model": config.rag_embedding_model,
                    "candidate_count": config.rag_candidate_count,
                    "split": args.split, "case_ids": [case.id for case in cases],
                    "label_status": "draft_source_checked", "human_label_reviewed": False}
        fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
        stopped = False
        for variant in VARIANTS:
            path = directory / variant / "latest.json"
            prior = json.loads(path.read_text()) if path.exists() else None
            if prior and prior.get("fingerprint") != fingerprint:
                raise ValueError("Resume fingerprint mismatch; choose a new output directory")
            previous = {row["id"]: row for row in prior["results"]} if prior else {}
            results = []
            base = hosted if variant == "file_search" else hybrid
            hybrid.config.rag_rerank = variant == "hybrid"
            for case in cases:
                if case.id in previous and not previous[case.id]["error"]:
                    results.append(previous[case.id])
                elif stopped:
                    result = run_evaluation(SimpleNamespace(chat=lambda **_: (_ for _ in ()).throw(
                        BudgetExceeded("Run stopped"))), [case])["results"][0]
                    results.append(result)
                else:
                    result = run_evaluation(base, [case])["results"][0]
                    results.append(result)
                    # Never automatically retry billable/uncertain calls in this runner.
                    if result["error"]:
                        stopped = True
                report = {"generated_at": datetime.now(timezone.utc).isoformat(),
                          "scoring_version": 2, "fingerprint": fingerprint, "metadata": metadata,
                          "variant": variant,
                          "summary": summarise(EvalResult(**row) for row in results),
                          "results": results, "budget": dict(ledger.data)}
                write_report(report, path.parent)
                print(json.dumps({"variant": variant, "completed": len(results),
                                  "passed": report["summary"]["passed"],
                                  "blocked": report["summary"]["blocked"],
                                  "accounted_usd": round(ledger.data["accounted_usd"], 6)}),
                      flush=True)
        summary = compare(directory)
        print(json.dumps({"paired_cases": summary["paired_case_count"], "budget": ledger.data}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No provider bodies, store IDs, keys or prompts in terminal error output.
        print(json.dumps({"stopped": True, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None

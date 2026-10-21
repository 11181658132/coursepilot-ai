import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.evaluation import load_cases, resume_evaluation, run_evaluation, write_report
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.index_jobs import index_writer
from app.services.library import discover_documents, summarize_courses
from app.services.openai_kb import OpenAIKnowledgeBase
from app.services.state import load_state


def main() -> None:
    parser = argparse.ArgumentParser(description="CoursePilot AI maintenance commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="Synchronize course documents")
    sync_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    courses_parser = subparsers.add_parser("courses", help="List discovered courses")
    eval_parser = subparsers.add_parser("eval", help="Run the local RAG evaluation suite")
    eval_parser.add_argument(
        "--dataset", type=Path, default=Path("../evals/questions.jsonl")
    )
    eval_parser.add_argument(
        "--output-dir", type=Path
    )
    eval_parser.add_argument("--limit", type=int, help="Run only the first N cases")
    eval_parser.add_argument(
        "--resume", action="store_true", help="Retry only cases blocked in the latest report"
    )
    for command_parser in (sync_parser, courses_parser, eval_parser):
        command_parser.add_argument("--engine", choices=("file_search", "hybrid"))
    eval_parser.add_argument("--no-rerank", action="store_true",
                             help="Ablate the hybrid reranker for comparison")
    args = parser.parse_args()
    settings = get_settings()
    engine = args.engine or settings.rag_engine
    if args.command == "eval" and args.no_rerank:
        settings.rag_rerank = False

    if args.command == "courses":
        state = ({"files": HybridKnowledgeBase(settings).manifest()}
                 if engine == "hybrid" else load_state(settings.index_state_path))
        result = summarize_courses(
            discover_documents(settings.knowledge_base_dir), state["files"].keys()
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "sync" and args.dry_run:
        if engine == "hybrid":
            print(HybridKnowledgeBase(settings).sync(
                settings.knowledge_base_dir, dry_run=True).model_dump_json(indent=2))
            return
        state = load_state(settings.index_state_path)
        files = state["files"]
        result = []
        for document in discover_documents(settings.knowledge_base_dir):
            existing = files.get(document.relative_path)
            if existing and existing.get("sha256") == document.sha256:
                action = "skip"
            else:
                action = "replace" if existing else "upload"
            result.append({"path": document.relative_path, "action": action})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    knowledge_base = HybridKnowledgeBase(settings) if engine == "hybrid" else OpenAIKnowledgeBase(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        state_path=settings.index_state_path,
        max_results=settings.max_search_results,
        telemetry_path=settings.telemetry_path,
        input_cost_per_million=settings.openai_input_cost_per_million,
        output_cost_per_million=settings.openai_output_cost_per_million,
        file_search_cost_per_thousand=settings.openai_file_search_cost_per_thousand,
    )
    if args.command == "eval":
        variant = "hybrid" if settings.rag_rerank else "hybrid-no-rerank"
        if args.output_dir is None:
            args.output_dir = Path("../evals/reports")
            if engine == "hybrid":
                args.output_dir = args.output_dir / variant
        cases = load_cases(args.dataset)
        if args.limit:
            cases = cases[: args.limit]
        latest_report = args.output_dir / "latest.json"
        if args.resume and latest_report.is_file():
            previous = json.loads(latest_report.read_text(encoding="utf-8"))
            expected = f"{engine}:{settings.rag_rerank}" if engine == "hybrid" else engine
            if previous.get("engine", "file_search") != expected:
                parser.error("Cannot resume a report from a different retrieval configuration")
            report = resume_evaluation(knowledge_base, cases, previous)
        else:
            report = run_evaluation(knowledge_base, cases)
        report["engine"] = f"{engine}:{settings.rag_rerank}" if engine == "hybrid" else engine
        paths = write_report(report, args.output_dir)
        print(
            json.dumps(
                {
                    "summary": report["summary"],
                    "reports": {key: str(value) for key, value in paths.items()},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    with index_writer(settings):
        report = knowledge_base.sync(settings.knowledge_base_dir)
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()

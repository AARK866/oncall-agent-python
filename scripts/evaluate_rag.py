import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.rag import KnowledgeBase
from app.rag.evaluation import RagRetrievalEvaluator, load_evaluation_cases


def main() -> int:
    args = _parse_args()
    _apply_overrides(args)
    cases = load_evaluation_cases(args.dataset)
    knowledge_base = KnowledgeBase.from_directory(
        settings.knowledge_local_path,
        chunk_size=settings.knowledge_ingest_chunk_size,
        chunk_overlap=settings.knowledge_ingest_chunk_overlap,
        retriever_mode=settings.knowledge_retriever_mode,
    )
    report = RagRetrievalEvaluator(knowledge_base).evaluate(cases, top_k=args.top_k)
    passed_thresholds = report.hit_rate >= args.min_hit_rate and report.mrr >= args.min_mrr

    payload = {
        "status": "pass" if passed_thresholds else "fail",
        "thresholds": {
            "min_hit_rate": args.min_hit_rate,
            "min_mrr": args.min_mrr,
        },
        "configuration": {
            "knowledge_engine": settings.knowledge_engine,
            "retriever_mode": settings.knowledge_retriever_mode,
            "vector_store": settings.knowledge_vector_store,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "reranker": settings.knowledge_reranker,
        },
        "report": report.model_dump(mode="json"),
    }
    _print_report(payload, as_json=args.json)
    return 0 if passed_thresholds else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate OnCall Agent retrieval quality.")
    parser.add_argument(
        "--dataset",
        default="app/data/evaluation/rag_cases.jsonl",
        help="JSONL evaluation dataset path.",
    )
    parser.add_argument("--top-k", type=int, help="Override top_k for every evaluation case.")
    parser.add_argument("--retriever-mode", choices=["keyword", "vector", "hybrid"])
    parser.add_argument("--knowledge-engine", choices=["local", "llamaindex"])
    parser.add_argument("--reranker", choices=["none", "llamaindex"])
    parser.add_argument("--min-hit-rate", type=float, default=0.8)
    parser.add_argument("--min-mrr", type=float, default=0.7)
    parser.add_argument(
        "--local-safe",
        action="store_true",
        help="Use hash embeddings and in-memory storage without external services.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def _apply_overrides(args: argparse.Namespace) -> None:
    if args.local_safe:
        settings.embedding_provider = "hash"
        settings.embedding_api_key = None
        settings.embedding_dimensions = 128
        settings.knowledge_vector_store = "in_memory"
        settings.knowledge_engine = args.knowledge_engine or "llamaindex"
        settings.knowledge_retriever_mode = args.retriever_mode or "vector"

    if args.retriever_mode:
        settings.knowledge_retriever_mode = args.retriever_mode
    if args.knowledge_engine:
        settings.knowledge_engine = args.knowledge_engine
    if args.reranker:
        settings.knowledge_reranker = args.reranker


def _print_report(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    report = payload["report"]
    configuration = payload["configuration"]
    print("RAG retrieval evaluation")
    print(f"- status: {payload['status']}")
    print(f"- engine: {configuration['knowledge_engine']}")
    print(f"- retriever: {configuration['retriever_mode']}")
    print(f"- vector_store: {configuration['vector_store']}")
    print(f"- reranker: {configuration['reranker']}")
    print(f"- cases: {report['total_cases']}")
    print(f"- hit_rate: {report['hit_rate']:.4f} (minimum {payload['thresholds']['min_hit_rate']:.4f})")
    print(f"- mrr: {report['mrr']:.4f} (minimum {payload['thresholds']['min_mrr']:.4f})")
    print("")
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        retrieved = ", ".join(result["retrieved_doc_ids"]) or "none"
        print(f"- [{status}] {result['case_id']}: retrieved={retrieved}, mrr={result['mrr']:.4f}")


if __name__ == "__main__":
    raise SystemExit(main())

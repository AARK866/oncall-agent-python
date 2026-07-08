import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag import KnowledgeIngestionPipeline


async def main() -> int:
    args = _parse_args()
    result = await KnowledgeIngestionPipeline().ingest(
        source=args.source,
        path=args.path,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    print("Knowledge ingestion")
    print(f"- status: {result.status}")
    print(f"- source: {result.source}")
    print(f"- path: {result.path}")
    print(f"- documents_loaded: {result.documents_loaded}")
    print(f"- chunks_created: {result.chunks_created}")
    print(f"- vector_store: {result.vector_store}")
    print(f"- collection_name: {result.collection_name}")
    print("- document_ids:")
    for doc_id in result.document_ids:
        print(f"  - {doc_id}")
    return 0 if result.status == "ok" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest runbooks into the configured knowledge vector store.")
    parser.add_argument("--source", choices=["local", "github"], help="Knowledge source. Defaults to KNOWLEDGE_SOURCE.")
    parser.add_argument("--path", help="Local directory or GitHub repository path. Defaults to knowledge settings.")
    parser.add_argument("--chunk-size", type=int, help="Chunk size for markdown splitting.")
    parser.add_argument("--chunk-overlap", type=int, help="Chunk overlap for markdown splitting.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

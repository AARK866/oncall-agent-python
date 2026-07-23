import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.rag import KnowledgeIngestionPipeline, MilvusVectorStore, create_embedding_model
from app.rag.access_control import system_access_context
from app.security import redact_text
from app.storage import SQLiteKnowledgeManifestStore


async def main() -> int:
    try:
        _validate_real_configuration()
        result = await _run_isolated_acceptance()
    except Exception as exc:
        print("Real knowledge pipeline acceptance")
        print(f"- status: FAIL")
        print(f"- error: {redact_text(f'{type(exc).__name__}: {exc}')}")
        return 1

    print("Real knowledge pipeline acceptance")
    print("- status: PASS")
    print(f"- collection: {result['collection']}")
    print(f"- embedding: {settings.embedding_provider}/{settings.embedding_model}")
    print(f"- first_ingest_chunks: {result['first_chunks']}")
    print(f"- unchanged_documents: {result['unchanged_documents']}")
    print(f"- updated_documents: {result['updated_documents']}")
    print(f"- retrieved_chunks: {result['retrieved_chunks']}")
    print(f"- total_elapsed_ms: {result['total_elapsed_ms']}")
    print("- cleanup: isolated Milvus collection removed")
    return 0


async def _run_isolated_acceptance() -> dict[str, int | str]:
    original_collection = settings.milvus_collection_name
    original_incremental = settings.knowledge_incremental_indexing_enabled
    collection_name = f"oncall_acceptance_{uuid4().hex[:12]}"
    settings.milvus_collection_name = collection_name
    settings.knowledge_incremental_indexing_enabled = True

    store = None
    collection_created = False
    try:
        with TemporaryDirectory(prefix="oncall-rag-acceptance-") as temporary_dir:
            root = Path(temporary_dir)
            runbooks = root / "runbooks"
            runbooks.mkdir()
            runbook = runbooks / "acceptance.md"
            marker = f"acceptance-{uuid4().hex}"
            runbook.write_text(
                f"# Real pipeline acceptance\n\n{marker} payment 5xx database recovery.",
                encoding="utf-8",
            )

            embedding_model = create_embedding_model()
            store = MilvusVectorStore(
                embedding_model=embedding_model,
                collection_name=collection_name,
            )
            store.ensure_collection()
            collection_created = True
            pipeline = KnowledgeIngestionPipeline(
                embedding_model=embedding_model,
                manifest_store=SQLiteKnowledgeManifestStore(root / "manifest.db"),
                milvus_store=store,
            )

            first = await pipeline.ingest(source="local", path=str(runbooks))
            second = await pipeline.ingest(source="local", path=str(runbooks))
            runbook.write_text(
                f"# Real pipeline acceptance\n\n{marker} updated rollback verification.",
                encoding="utf-8",
            )
            third = await pipeline.ingest(source="local", path=str(runbooks))
            results = store.search(
                f"{marker} updated rollback verification",
                top_k=3,
                access_context=system_access_context(),
            )

            first_stats = first.metadata["incremental"]
            second_stats = second.metadata["incremental"]
            third_stats = third.metadata["incremental"]
            _require(first_stats["new_documents"] == 1, "first run did not index one new document")
            _require(first.chunks_created > 0, "first run created no chunks")
            _require(second.chunks_created == 0, "unchanged run generated embeddings again")
            _require(second_stats["unchanged_documents"] == 1, "unchanged document was not detected")
            _require(third_stats["updated_documents"] == 1, "updated document was not reindexed")
            _require(results, "Milvus returned no vector search result")
            _require(
                any(marker in result.content and "updated rollback" in result.content for result in results),
                "Milvus search did not return the updated document",
            )

            return {
                "collection": collection_name,
                "first_chunks": first.chunks_created,
                "unchanged_documents": second_stats["unchanged_documents"],
                "updated_documents": third_stats["updated_documents"],
                "retrieved_chunks": len(results),
                "total_elapsed_ms": sum(
                    int(item.metadata["observability"]["elapsed_ms"])
                    for item in (first, second, third)
                ),
            }
    finally:
        if store is not None and collection_created:
            _drop_collection(store, collection_name)
        settings.milvus_collection_name = original_collection
        settings.knowledge_incremental_indexing_enabled = original_incremental


def _validate_real_configuration() -> None:
    if settings.embedding_provider.lower().strip() in {"hash", "mock"}:
        raise ValueError("Set EMBEDDING_PROVIDER to a real provider before running this check.")
    if settings.knowledge_vector_store.lower().strip() != "milvus":
        raise ValueError("Set KNOWLEDGE_VECTOR_STORE=milvus before running this check.")
    if not settings.milvus_uri:
        raise ValueError("MILVUS_URI is required.")


def _drop_collection(store: MilvusVectorStore, collection_name: str) -> None:
    try:
        if store.client.has_collection(collection_name=collection_name):
            store.client.drop_collection(collection_name=collection_name)
    finally:
        close = getattr(store.client, "close", None)
        if callable(close):
            close()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

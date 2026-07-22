import asyncio

from app.config import settings
from app.rag import HashEmbeddingModel, KnowledgeIngestionPipeline
from app.storage import SQLiteKnowledgeManifestStore, new_manifest_record


def test_sqlite_knowledge_manifest_store_applies_versions_atomically(tmp_path) -> None:
    store = SQLiteKnowledgeManifestStore(tmp_path / "manifest.db")
    first = new_manifest_record(
        namespace="local:test",
        doc_id="payment.md",
        source_uri="payment.md",
        source_version="v1",
        document_signature="doc-v1",
        index_signature="index-v1",
        chunk_ids=["payment.md#chunk-0"],
        metadata={"access_scope": "internal"},
    )
    store.apply("local:test", [first], [])

    saved = store.list_records("local:test")["payment.md"]
    assert saved.source_version == "v1"
    assert saved.chunk_ids == ["payment.md#chunk-0"]

    second = new_manifest_record(
        namespace="local:test",
        doc_id="order.md",
        source_uri="order.md",
        source_version="v2",
        document_signature="doc-v2",
        index_signature="index-v1",
        chunk_ids=["order.md#chunk-0"],
        metadata={"access_scope": "internal"},
    )
    store.apply("local:test", [second], ["payment.md"])

    records = store.list_records("local:test")
    assert set(records) == {"order.md"}


def test_incremental_ingestion_skips_unchanged_and_cleans_stale_chunks(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "knowledge_vector_store", "milvus")
    monkeypatch.setattr(settings, "knowledge_incremental_indexing_enabled", True)
    monkeypatch.setattr(settings, "embedding_dimensions", 16)
    monkeypatch.setattr(settings, "milvus_collection_name", "test_chunks")

    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    payment = runbooks / "payment.md"
    order = runbooks / "order.md"
    payment.write_text(
        "# Payment Runbook\n\n" + "Check payment 5xx database pool. " * 20,
        encoding="utf-8",
    )
    order.write_text("# Order Runbook\n\nCheck order timeout.", encoding="utf-8")

    vector_store = FakeMilvusStore()
    pipeline = KnowledgeIngestionPipeline(
        embedding_model=HashEmbeddingModel(dimensions=16),
        manifest_store=SQLiteKnowledgeManifestStore(tmp_path / "manifest.db"),
        milvus_store=vector_store,
    )

    first = asyncio.run(
        pipeline.ingest(
            source="local",
            path=str(runbooks),
            chunk_size=160,
            chunk_overlap=20,
        )
    )
    first_chunk_ids = {chunk.chunk_id for chunk in vector_store.upsert_batches[0]}
    first_stats = first.metadata["incremental"]
    assert first_stats["new_documents"] == 2
    assert first_stats["indexed_documents"] == 2
    assert first_stats["unchanged_documents"] == 0

    second = asyncio.run(
        pipeline.ingest(
            source="local",
            path=str(runbooks),
            chunk_size=160,
            chunk_overlap=20,
        )
    )
    second_stats = second.metadata["incremental"]
    assert second_stats["indexed_documents"] == 0
    assert second_stats["unchanged_documents"] == 2
    assert len(vector_store.upsert_batches) == 1

    payment.write_text("# Payment Runbook\n\nUse the rollback procedure.", encoding="utf-8")
    order.unlink()
    vector_store.operations.clear()

    third = asyncio.run(
        pipeline.ingest(
            source="local",
            path=str(runbooks),
            chunk_size=160,
            chunk_overlap=20,
        )
    )
    third_stats = third.metadata["incremental"]
    assert third_stats["updated_documents"] == 1
    assert third_stats["deleted_documents"] == 1
    assert third_stats["indexed_documents"] == 1
    assert {chunk.doc_id for chunk in vector_store.upsert_batches[-1]} == {"payment.md"}
    assert "order.md#chunk-0" in vector_store.deleted_chunk_ids
    assert vector_store.deleted_chunk_ids == sorted(
        first_chunk_ids - {"payment.md#chunk-0"}
    )
    assert vector_store.operations == ["ensure", "upsert", "delete"]


def test_full_rebuild_reindexes_unchanged_documents(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "knowledge_vector_store", "milvus")
    monkeypatch.setattr(settings, "knowledge_incremental_indexing_enabled", True)
    monkeypatch.setattr(settings, "embedding_dimensions", 16)

    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    (runbooks / "payment.md").write_text("# Payment\n\nCheck 5xx.", encoding="utf-8")
    vector_store = FakeMilvusStore()
    pipeline = KnowledgeIngestionPipeline(
        embedding_model=HashEmbeddingModel(dimensions=16),
        manifest_store=SQLiteKnowledgeManifestStore(tmp_path / "manifest.db"),
        milvus_store=vector_store,
    )

    asyncio.run(pipeline.ingest(source="local", path=str(runbooks)))
    result = asyncio.run(
        pipeline.ingest(source="local", path=str(runbooks), full_rebuild=True)
    )

    assert result.metadata["incremental"]["mode"] == "full_rebuild"
    assert result.metadata["incremental"]["updated_documents"] == 1
    assert len(vector_store.upsert_batches) == 2


class FakeMilvusStore:
    collection_name = "test_chunks"

    def __init__(self) -> None:
        self.upsert_batches = []
        self.deleted_chunk_ids: list[str] = []
        self.operations: list[str] = []

    def ensure_collection(self) -> None:
        self.operations.append("ensure")

    def upsert_chunks(self, chunks) -> None:
        if not chunks:
            return
        self.operations.append("upsert")
        self.upsert_batches.append(list(chunks))

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self.operations.append("delete")
        self.deleted_chunk_ids = list(chunk_ids)

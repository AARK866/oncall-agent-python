from typing import Any

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.rag import HashEmbeddingModel, KnowledgeIngestionPipeline
from app.schemas import KnowledgeIngestSource


client = TestClient(app)


def test_local_knowledge_ingestion_pipeline_loads_and_chunks_markdown(tmp_path) -> None:
    runbook_dir = tmp_path / "runbooks"
    runbook_dir.mkdir()
    (runbook_dir / "payment.md").write_text(
        "# Payment 5xx Runbook\n\n## Diagnosis\nCheck payment-api 5xx and database pool.",
        encoding="utf-8",
    )

    result = _run_async(
        KnowledgeIngestionPipeline(
            embedding_model=HashEmbeddingModel(dimensions=16),
        ).ingest(
            source=KnowledgeIngestSource.local,
            path=str(runbook_dir),
            chunk_size=200,
            chunk_overlap=20,
        )
    )

    assert result.status == "ok"
    assert result.source == KnowledgeIngestSource.local
    assert result.documents_loaded == 1
    assert result.chunks_created >= 1
    assert result.document_ids == ["payment.md"]
    assert result.metadata["persisted"] is False


def test_github_knowledge_ingestion_pipeline_loads_markdown_recursively() -> None:
    fake_github = FakeGitHubClient()
    result = _run_async(
        KnowledgeIngestionPipeline(
            embedding_model=HashEmbeddingModel(dimensions=16),
            github_client=fake_github,
        ).ingest(
            source=KnowledgeIngestSource.github,
            path="docs/runbooks",
            chunk_size=200,
            chunk_overlap=20,
        )
    )

    assert result.status == "ok"
    assert result.source == KnowledgeIngestSource.github
    assert result.documents_loaded == 2
    assert result.document_ids == ["payment.md", "nested/order.md"]
    assert result.metadata["persisted"] is False


def test_knowledge_ingest_api_accepts_local_path(tmp_path) -> None:
    runbook_dir = tmp_path / "runbooks"
    runbook_dir.mkdir()
    (runbook_dir / "order.md").write_text(
        "# Order Timeout Runbook\n\nCheck order-api timeout and latency.",
        encoding="utf-8",
    )

    response = client.post(
        "/api/knowledge/ingest",
        json={
            "source": "local",
            "path": str(runbook_dir),
            "chunk_size": 200,
            "chunk_overlap": 20,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["documents_loaded"] == 1
    assert data["chunks_created"] >= 1
    assert data["document_ids"] == ["order.md"]


def test_knowledge_ingestion_can_prepare_llamaindex_shapes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "knowledge_engine", "llamaindex")
    runbook_dir = tmp_path / "runbooks"
    runbook_dir.mkdir()
    (runbook_dir / "payment.md").write_text(
        "# Payment Runbook\n\nCheck payment-api 5xx and rollback procedure.",
        encoding="utf-8",
    )

    pipeline = CapturingKnowledgeIngestionPipeline(
        embedding_model=HashEmbeddingModel(dimensions=16),
    )
    result = _run_async(
        pipeline.ingest(
            source=KnowledgeIngestSource.local,
            path=str(runbook_dir),
            chunk_size=200,
            chunk_overlap=20,
        )
    )

    assert result.metadata["knowledge_engine"] == "llamaindex"
    assert result.metadata["llamaindex"]["engine"] == "llamaindex"
    assert result.metadata["llamaindex"]["pipeline"] == "document-node-store"
    assert result.metadata["llamaindex"]["documents_prepared"] == 1
    assert result.metadata["llamaindex"]["nodes_prepared"] == result.chunks_created
    assert result.metadata["llamaindex"]["chunks_normalized"] == result.chunks_created
    assert pipeline.stored_chunks
    assert pipeline.stored_chunks[0].metadata["knowledge_engine"] == "llamaindex"
    assert pipeline.stored_chunks[0].metadata["doc_id"] == "payment.md"


class CapturingKnowledgeIngestionPipeline(KnowledgeIngestionPipeline):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.stored_chunks = []

    def _upsert_chunks(self, chunks):
        self.stored_chunks = chunks
        return {"store": "capture", "persisted": False}


class FakeGitHubClient:
    repo = "example/oncall"
    branch = "main"

    async def get_file(self, path: str, ref: str | None = None) -> dict[str, Any]:
        if path == "docs/runbooks":
            return {
                "type": "directory",
                "entries": [
                    {"path": "docs/runbooks/payment.md", "type": "file"},
                    {"path": "docs/runbooks/nested", "type": "dir"},
                    {"path": "docs/runbooks/ignore.txt", "type": "file"},
                ],
            }
        if path == "docs/runbooks/nested":
            return {
                "type": "directory",
                "entries": [
                    {"path": "docs/runbooks/nested/order.md", "type": "file"},
                ],
            }
        if path == "docs/runbooks/payment.md":
            return {
                "type": "file",
                "path": path,
                "ref": ref or self.branch,
                "sha": "sha-payment",
                "content": "# Payment Runbook\n\nPayment 5xx database recovery.",
            }
        if path == "docs/runbooks/nested/order.md":
            return {
                "type": "file",
                "path": path,
                "ref": ref or self.branch,
                "sha": "sha-order",
                "content": "# Order Runbook\n\nOrder timeout recovery.",
            }
        raise AssertionError(f"Unexpected GitHub path: {path}")


def _run_async(awaitable):
    import asyncio

    return asyncio.run(awaitable)

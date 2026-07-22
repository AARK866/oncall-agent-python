import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import (
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeIngestSource,
    KnowledgeIngestionTaskStatus,
)
from app.storage import SQLiteKnowledgeTaskStore
from app.tasks import KnowledgeIngestionQueue


client = TestClient(app)


def test_knowledge_ingestion_queue_persists_progress_and_result(tmp_path) -> None:
    pipeline = SuccessfulPipeline()
    queue = KnowledgeIngestionQueue(
        task_store=SQLiteKnowledgeTaskStore(tmp_path / "tasks.db"),
        pipeline_factory=lambda: pipeline,
    )
    task = queue.submit(KnowledgeIngestRequest(source="local", path="runbooks"))

    result = asyncio.run(queue.run(task.task_id))
    duplicate = asyncio.run(queue.run(task.task_id))

    assert result.status == KnowledgeIngestionTaskStatus.succeeded
    assert result.attempt == 1
    assert result.progress_stage == "completed"
    assert result.progress_percent == 100
    assert result.result is not None
    assert result.result.documents_loaded == 2
    assert duplicate == result
    assert pipeline.calls == 1


def test_failed_knowledge_ingestion_task_can_retry(tmp_path) -> None:
    pipeline = FailOncePipeline()
    store = SQLiteKnowledgeTaskStore(tmp_path / "tasks.db")
    queue = KnowledgeIngestionQueue(
        task_store=store,
        pipeline_factory=lambda: pipeline,
    )
    task = queue.submit(KnowledgeIngestRequest(source="local", path="runbooks"))

    failed = asyncio.run(queue.run(task.task_id))
    queued_again = queue.retry(task.task_id)
    succeeded = asyncio.run(queue.run(task.task_id))

    assert failed.status == KnowledgeIngestionTaskStatus.failed
    assert "temporary embedding failure" in (failed.error or "")
    assert queued_again.status == KnowledgeIngestionTaskStatus.queued
    assert succeeded.status == KnowledgeIngestionTaskStatus.succeeded
    assert succeeded.attempt == 2
    assert SQLiteKnowledgeTaskStore(tmp_path / "tasks.db").get_task(task.task_id) == succeeded


def test_knowledge_ingestion_retry_limit_is_enforced(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "knowledge_ingestion_max_attempts", 1)
    queue = KnowledgeIngestionQueue(
        task_store=SQLiteKnowledgeTaskStore(tmp_path / "tasks.db"),
        pipeline_factory=AlwaysFailingPipeline,
    )
    task = queue.submit(KnowledgeIngestRequest(source="local", path="runbooks"))
    asyncio.run(queue.run(task.task_id))

    with pytest.raises(ValueError, match="1-attempt limit"):
        queue.retry(task.task_id)


def test_knowledge_ingestion_task_api_runs_and_exposes_status(
    tmp_path,
    monkeypatch,
) -> None:
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    (runbooks / "payment.md").write_text(
        "# Payment Runbook\n\nCheck payment 5xx.",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings,
        "knowledge_ingestion_task_db_path",
        str(tmp_path / "api-tasks.db"),
    )

    response = client.post(
        "/api/knowledge/ingestion-tasks",
        json={"source": "local", "path": str(runbooks)},
    )

    assert response.status_code == 202
    task_id = response.json()["task_id"]
    status_response = client.get(f"/api/knowledge/ingestion-tasks/{task_id}")
    assert status_response.status_code == 200
    task = status_response.json()
    assert task["status"] == "succeeded"
    assert task["progress_percent"] == 100
    assert task["result"]["documents_loaded"] == 1

    list_response = client.get("/api/knowledge/ingestion-tasks")
    assert list_response.status_code == 200
    assert list_response.json()[0]["task_id"] == task_id


class SuccessfulPipeline:
    def __init__(self) -> None:
        self.calls = 0

    async def ingest(self, **kwargs) -> KnowledgeIngestResponse:
        self.calls += 1
        kwargs["progress_callback"]("documents_loaded", 30)
        kwargs["progress_callback"]("vectors_persisted", 90)
        return _success_response()


class FailOncePipeline(SuccessfulPipeline):
    async def ingest(self, **kwargs) -> KnowledgeIngestResponse:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary embedding failure")
        return _success_response()


class AlwaysFailingPipeline:
    async def ingest(self, **kwargs) -> KnowledgeIngestResponse:
        raise RuntimeError("Milvus unavailable")


def _success_response() -> KnowledgeIngestResponse:
    return KnowledgeIngestResponse(
        status="ok",
        source=KnowledgeIngestSource.local,
        path="runbooks",
        documents_loaded=2,
        chunks_created=4,
        vector_store="milvus",
        collection_name="test_chunks",
        document_ids=["payment.md", "order.md"],
    )

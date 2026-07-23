from __future__ import annotations

import logging
from collections.abc import Callable

from app.config import settings
from app.rag.ingestion import KnowledgeIngestionPipeline
from app.schemas import (
    KnowledgeIngestRequest,
    KnowledgeIngestionAttemptRecord,
    KnowledgeIngestionMetricsResponse,
    KnowledgeIngestionTaskRecord,
)
from app.storage import SQLiteKnowledgeTaskStore

logger = logging.getLogger(__name__)


class KnowledgeIngestionQueue:
    """Persistent task coordinator for knowledge ingestion work."""

    def __init__(
        self,
        task_store: SQLiteKnowledgeTaskStore | None = None,
        pipeline_factory: Callable[[], KnowledgeIngestionPipeline] | None = None,
    ) -> None:
        self.task_store = task_store or SQLiteKnowledgeTaskStore.from_settings()
        self.pipeline_factory = pipeline_factory or KnowledgeIngestionPipeline

    def submit(self, request: KnowledgeIngestRequest) -> KnowledgeIngestionTaskRecord:
        task = self.task_store.create_task(request)
        logger.info(
            "knowledge_ingestion_submitted task_id=%s source=%s path=%s",
            task.task_id,
            request.source or settings.knowledge_source,
            request.path or "<default>",
        )
        return task

    async def run(self, task_id: str) -> KnowledgeIngestionTaskRecord:
        task = self.task_store.claim(task_id)
        if task is None:
            return self.task_store.require_task(task_id)

        logger.info(
            "knowledge_ingestion_started task_id=%s attempt=%s",
            task_id,
            task.attempt,
        )
        try:
            result = await self.pipeline_factory().ingest(
                source=task.request.source,
                path=task.request.path,
                chunk_size=task.request.chunk_size,
                chunk_overlap=task.request.chunk_overlap,
                full_rebuild=task.request.full_rebuild,
                progress_callback=lambda stage, percent: self.task_store.update_progress(
                    task_id,
                    stage,
                    percent,
                ),
            )
        except Exception as exc:
            failed = self.task_store.mark_failed(
                task_id,
                f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "knowledge_ingestion_failed task_id=%s attempt=%s error_type=%s",
                task_id,
                failed.attempt,
                type(exc).__name__,
            )
            return failed
        succeeded = self.task_store.mark_succeeded(task_id, result)
        logger.info(
            "knowledge_ingestion_succeeded task_id=%s attempt=%s documents=%s chunks=%s elapsed_ms=%s",
            task_id,
            succeeded.attempt,
            result.documents_loaded,
            result.chunks_created,
            result.metadata.get("observability", {}).get("elapsed_ms"),
        )
        return succeeded

    def retry(self, task_id: str) -> KnowledgeIngestionTaskRecord:
        return self.task_store.requeue(
            task_id,
            max_attempts=settings.knowledge_ingestion_max_attempts,
        )

    def get(self, task_id: str) -> KnowledgeIngestionTaskRecord | None:
        return self.task_store.get_task(task_id)

    def list(self, limit: int = 20) -> list[KnowledgeIngestionTaskRecord]:
        return self.task_store.list_tasks(limit=limit)

    def attempts(self, task_id: str) -> list[KnowledgeIngestionAttemptRecord]:
        self.task_store.require_task(task_id)
        return self.task_store.list_attempts(task_id)

    def metrics(self, window_hours: int = 24) -> KnowledgeIngestionMetricsResponse:
        return self.task_store.metrics(window_hours=window_hours)

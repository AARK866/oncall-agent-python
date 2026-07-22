from __future__ import annotations

from collections.abc import Callable

from app.config import settings
from app.rag.ingestion import KnowledgeIngestionPipeline
from app.schemas import (
    KnowledgeIngestRequest,
    KnowledgeIngestionTaskRecord,
)
from app.storage import SQLiteKnowledgeTaskStore


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
        return self.task_store.create_task(request)

    async def run(self, task_id: str) -> KnowledgeIngestionTaskRecord:
        task = self.task_store.claim(task_id)
        if task is None:
            return self.task_store.require_task(task_id)

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
            return self.task_store.mark_failed(
                task_id,
                f"{type(exc).__name__}: {exc}",
            )
        return self.task_store.mark_succeeded(task_id, result)

    def retry(self, task_id: str) -> KnowledgeIngestionTaskRecord:
        return self.task_store.requeue(
            task_id,
            max_attempts=settings.knowledge_ingestion_max_attempts,
        )

    def get(self, task_id: str) -> KnowledgeIngestionTaskRecord | None:
        return self.task_store.get_task(task_id)

    def list(self, limit: int = 20) -> list[KnowledgeIngestionTaskRecord]:
        return self.task_store.list_tasks(limit=limit)

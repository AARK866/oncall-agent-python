from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import (
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeIngestionAttemptRecord,
    KnowledgeIngestionMetricsResponse,
    KnowledgeIngestionTaskRecord,
    KnowledgeIngestionTaskStatus,
)
from app.storage.database import (
    Database,
    DatabaseConnection,
    DatabaseRow,
    configured_database_target,
)


class SQLiteKnowledgeTaskStore:
    """Knowledge task repository backed by the configured database."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        auto_create_schema: bool = True,
    ) -> None:
        self.db_path = db_path
        self.database = Database(db_path)
        if auto_create_schema:
            self._init_schema()

    @classmethod
    def from_settings(cls) -> "SQLiteKnowledgeTaskStore":
        return cls(
            configured_database_target(settings.knowledge_ingestion_task_db_path),
            auto_create_schema=settings.database_auto_create_schema,
        )

    def create_task(self, request: KnowledgeIngestRequest) -> KnowledgeIngestionTaskRecord:
        now = datetime.utcnow()
        task = KnowledgeIngestionTaskRecord(
            task_id=f"kingest_{uuid4().hex}",
            status=KnowledgeIngestionTaskStatus.queued,
            request=request,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_ingestion_tasks (
                    task_id, status, request_json, attempt, progress_stage,
                    progress_percent, result_json, error, created_at,
                    updated_at, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _task_values(task),
            )
        return task

    def claim(self, task_id: str) -> KnowledgeIngestionTaskRecord | None:
        now = datetime.utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_ingestion_tasks
                SET status = ?, attempt = attempt + 1,
                    progress_stage = ?, progress_percent = ?, error = NULL,
                    updated_at = ?, started_at = ?, finished_at = NULL
                WHERE task_id = ? AND status = ?
                """,
                (
                    KnowledgeIngestionTaskStatus.running.value,
                    "starting",
                    5,
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                    KnowledgeIngestionTaskStatus.queued.value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT attempt FROM knowledge_ingestion_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO knowledge_ingestion_attempts (
                    task_id, attempt, status, progress_stage, result_json,
                    error, elapsed_ms, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
                """,
                (
                    task_id,
                    int(row["attempt"]),
                    KnowledgeIngestionTaskStatus.running.value,
                    "starting",
                    now.isoformat(),
                ),
            )
        return self.require_task(task_id)

    def update_progress(self, task_id: str, stage: str, percent: int) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE knowledge_ingestion_tasks
                SET progress_stage = ?, progress_percent = ?, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    stage,
                    max(0, min(percent, 99)),
                    now,
                    task_id,
                    KnowledgeIngestionTaskStatus.running.value,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_ingestion_attempts
                SET progress_stage = ?
                WHERE task_id = ? AND attempt = (
                    SELECT attempt FROM knowledge_ingestion_tasks WHERE task_id = ?
                )
                """,
                (stage, task_id, task_id),
            )

    def mark_succeeded(
        self,
        task_id: str,
        result: KnowledgeIngestResponse,
    ) -> KnowledgeIngestionTaskRecord:
        task = self.require_task(task_id)
        now = datetime.utcnow()
        elapsed_ms = _elapsed_ms(task.started_at, now)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE knowledge_ingestion_tasks
                SET status = ?, progress_stage = ?, progress_percent = ?,
                    result_json = ?, error = NULL, updated_at = ?, finished_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    KnowledgeIngestionTaskStatus.succeeded.value,
                    "completed",
                    100,
                    result.model_dump_json(),
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                    KnowledgeIngestionTaskStatus.running.value,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_ingestion_attempts
                SET status = ?, progress_stage = ?, result_json = ?,
                    error = NULL, elapsed_ms = ?, finished_at = ?
                WHERE task_id = ? AND attempt = ?
                """,
                (
                    KnowledgeIngestionTaskStatus.succeeded.value,
                    "completed",
                    result.model_dump_json(),
                    elapsed_ms,
                    now.isoformat(),
                    task_id,
                    task.attempt,
                ),
            )
        return self.require_task(task_id)

    def mark_failed(self, task_id: str, error: str) -> KnowledgeIngestionTaskRecord:
        task = self.require_task(task_id)
        now = datetime.utcnow()
        elapsed_ms = _elapsed_ms(task.started_at, now)
        normalized_error = error[:4000]
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE knowledge_ingestion_tasks
                SET status = ?, progress_stage = ?, error = ?,
                    updated_at = ?, finished_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    KnowledgeIngestionTaskStatus.failed.value,
                    "failed",
                    normalized_error,
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                    KnowledgeIngestionTaskStatus.running.value,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_ingestion_attempts
                SET status = ?, progress_stage = ?, error = ?,
                    elapsed_ms = ?, finished_at = ?
                WHERE task_id = ? AND attempt = ?
                """,
                (
                    KnowledgeIngestionTaskStatus.failed.value,
                    "failed",
                    normalized_error,
                    elapsed_ms,
                    now.isoformat(),
                    task_id,
                    task.attempt,
                ),
            )
        return self.require_task(task_id)

    def requeue(self, task_id: str, max_attempts: int) -> KnowledgeIngestionTaskRecord:
        task = self.require_task(task_id)
        if task.status != KnowledgeIngestionTaskStatus.failed:
            raise ValueError("Only failed knowledge ingestion tasks can be retried.")
        if task.attempt >= max_attempts:
            raise ValueError(f"Knowledge ingestion task reached the {max_attempts}-attempt limit.")

        now = datetime.utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE knowledge_ingestion_tasks
                SET status = ?, progress_stage = ?, progress_percent = ?,
                    result_json = NULL, error = NULL, updated_at = ?,
                    started_at = NULL, finished_at = NULL
                WHERE task_id = ? AND status = ?
                """,
                (
                    KnowledgeIngestionTaskStatus.queued.value,
                    "queued_for_retry",
                    0,
                    now,
                    task_id,
                    KnowledgeIngestionTaskStatus.failed.value,
                ),
            )
        return self.require_task(task_id)

    def get_task(self, task_id: str) -> KnowledgeIngestionTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_ingestion_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return _task_from_row(row) if row is not None else None

    def require_task(self, task_id: str) -> KnowledgeIngestionTaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def list_tasks(self, limit: int = 20) -> list[KnowledgeIngestionTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_attempts(self, task_id: str) -> list[KnowledgeIngestionAttemptRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_attempts
                WHERE task_id = ?
                ORDER BY attempt ASC
                """,
                (task_id,),
            ).fetchall()
        return [_attempt_from_row(row) for row in rows]

    def metrics(self, window_hours: int = 24) -> KnowledgeIngestionMetricsResponse:
        cutoff = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_tasks
                WHERE created_at >= ?
                ORDER BY created_at ASC
                """,
                (cutoff,),
            ).fetchall()
        tasks = [_task_from_row(row) for row in rows]
        by_status = {
            status.value: sum(task.status == status for task in tasks)
            for status in KnowledgeIngestionTaskStatus
        }
        terminal_count = by_status["succeeded"] + by_status["failed"]
        durations = sorted(
            _elapsed_ms(task.started_at, task.finished_at)
            for task in tasks
            if task.started_at is not None and task.finished_at is not None
        )
        results = [task.result for task in tasks if task.result is not None]
        observability = [result.metadata.get("observability", {}) for result in results]
        p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
        return KnowledgeIngestionMetricsResponse(
            window_hours=window_hours,
            total_tasks=len(tasks),
            by_status=by_status,
            success_rate=(
                round(by_status["succeeded"] / terminal_count, 4)
                if terminal_count
                else 0.0
            ),
            average_duration_ms=(
                round(sum(durations) / len(durations), 2)
                if durations
                else None
            ),
            p95_duration_ms=durations[p95_index] if durations else None,
            retried_tasks=sum(task.attempt > 1 for task in tasks),
            total_attempts=sum(task.attempt for task in tasks),
            documents_processed=sum(result.documents_loaded for result in results),
            chunks_created=sum(result.chunks_created for result in results),
            vectors_upserted=sum(
                int(item.get("vectors_upserted", 0))
                for item in observability
            ),
            stale_vectors_deleted=sum(
                int(item.get("stale_vectors_deleted", 0))
                for item in observability
            ),
        )

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_ingestion_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    progress_stage TEXT NOT NULL,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_ingestion_attempts (
                    task_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress_stage TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    elapsed_ms INTEGER,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    PRIMARY KEY (task_id, attempt),
                    FOREIGN KEY (task_id) REFERENCES knowledge_ingestion_tasks(task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_knowledge_ingestion_attempts_status
                ON knowledge_ingestion_attempts(status, started_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_knowledge_ingestion_tasks_status_created
                ON knowledge_ingestion_tasks(status, created_at DESC)
                """
            )

    def _connect(self) -> DatabaseConnection:
        return self.database.connect()


def _task_values(task: KnowledgeIngestionTaskRecord) -> tuple:
    return (
        task.task_id,
        task.status.value,
        task.request.model_dump_json(),
        task.attempt,
        task.progress_stage,
        task.progress_percent,
        task.result.model_dump_json() if task.result else None,
        task.error,
        task.created_at.isoformat(),
        task.updated_at.isoformat(),
        task.started_at.isoformat() if task.started_at else None,
        task.finished_at.isoformat() if task.finished_at else None,
    )


def _task_from_row(row: DatabaseRow) -> KnowledgeIngestionTaskRecord:
    result_json = row["result_json"]
    return KnowledgeIngestionTaskRecord(
        task_id=row["task_id"],
        status=KnowledgeIngestionTaskStatus(row["status"]),
        request=KnowledgeIngestRequest.model_validate(json.loads(row["request_json"])),
        attempt=int(row["attempt"]),
        progress_stage=row["progress_stage"],
        progress_percent=int(row["progress_percent"]),
        result=(
            KnowledgeIngestResponse.model_validate(json.loads(result_json))
            if result_json
            else None
        ),
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        started_at=(
            datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None
        ),
        finished_at=(
            datetime.fromisoformat(row["finished_at"])
            if row["finished_at"]
            else None
        ),
    )


def _attempt_from_row(row: DatabaseRow) -> KnowledgeIngestionAttemptRecord:
    result_json = row["result_json"]
    return KnowledgeIngestionAttemptRecord(
        task_id=row["task_id"],
        attempt=int(row["attempt"]),
        status=KnowledgeIngestionTaskStatus(row["status"]),
        progress_stage=row["progress_stage"],
        result=(
            KnowledgeIngestResponse.model_validate(json.loads(result_json))
            if result_json
            else None
        ),
        error=row["error"],
        elapsed_ms=int(row["elapsed_ms"]) if row["elapsed_ms"] is not None else None,
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=(
            datetime.fromisoformat(row["finished_at"])
            if row["finished_at"]
            else None
        ),
    )


def _elapsed_ms(started_at: datetime | None, finished_at: datetime | None) -> int:
    if started_at is None or finished_at is None:
        return 0
    return max(0, round((finished_at - started_at).total_seconds() * 1000))

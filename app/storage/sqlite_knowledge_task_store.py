from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import (
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeIngestionTaskRecord,
    KnowledgeIngestionTaskStatus,
)


class SQLiteKnowledgeTaskStore:
    """Persistent state for asynchronous knowledge ingestion tasks."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @classmethod
    def from_settings(cls) -> "SQLiteKnowledgeTaskStore":
        return cls(settings.knowledge_ingestion_task_db_path)

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
        now = datetime.utcnow().isoformat()
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
                    now,
                    now,
                    task_id,
                    KnowledgeIngestionTaskStatus.queued.value,
                ),
            )
        if cursor.rowcount == 0:
            return None
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

    def mark_succeeded(
        self,
        task_id: str,
        result: KnowledgeIngestResponse,
    ) -> KnowledgeIngestionTaskRecord:
        now = datetime.utcnow().isoformat()
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
                    now,
                    now,
                    task_id,
                    KnowledgeIngestionTaskStatus.running.value,
                ),
            )
        return self.require_task(task_id)

    def mark_failed(self, task_id: str, error: str) -> KnowledgeIngestionTaskRecord:
        now = datetime.utcnow().isoformat()
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
                    error[:4000],
                    now,
                    now,
                    task_id,
                    KnowledgeIngestionTaskStatus.running.value,
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
                CREATE INDEX IF NOT EXISTS idx_knowledge_ingestion_tasks_status_created
                ON knowledge_ingestion_tasks(status, created_at DESC)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


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


def _task_from_row(row: sqlite3.Row) -> KnowledgeIngestionTaskRecord:
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

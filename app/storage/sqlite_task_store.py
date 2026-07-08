import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.schemas import AlertSeverity, ChatResponse, DiagnosisTaskRecord, DiagnosisTaskStatus


class SQLiteTaskStore:
    """Persistent diagnosis task queue state backed by SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @classmethod
    def from_settings(cls) -> "SQLiteTaskStore":
        return cls(settings.incident_db_path)

    def create_task(
        self,
        source: str,
        question: str,
        session_id: str,
        service: str | None = None,
        severity: AlertSeverity = AlertSeverity.warning,
        labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        task = DiagnosisTaskRecord(
            task_id=f"task_{uuid4().hex}",
            source=source,
            status=DiagnosisTaskStatus.queued,
            question=question,
            session_id=session_id,
            service=service,
            severity=severity,
            labels=labels or {},
            trigger_metadata=trigger_metadata or {},
            created_at=now,
            updated_at=now,
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnosis_tasks (
                    task_id, source, status, question, session_id, service, severity,
                    labels_json, trigger_metadata_json, result_json, incident_id,
                    diagnosis_id, error, created_at, updated_at, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _task_values(task),
            )

        return task

    def mark_running(self, task_id: str) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, updated_at = ?, started_at = ?
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.running.value,
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        return self.require_task(task_id)

    def mark_succeeded(self, task_id: str, response: ChatResponse) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, result_json = ?, incident_id = ?, diagnosis_id = ?,
                    error = NULL, updated_at = ?, finished_at = ?
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.succeeded.value,
                    _json_dumps(response.model_dump(mode="json")),
                    response.metadata.get("incident_id"),
                    response.metadata.get("diagnosis_id"),
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        return self.require_task(task_id)

    def mark_failed(self, task_id: str, error: str) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, error = ?, updated_at = ?, finished_at = ?
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.failed.value,
                    error[:4000],
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        return self.require_task(task_id)

    def get_task(self, task_id: str) -> DiagnosisTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnosis_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return _task_from_row(row) if row else None

    def require_task(self, task_id: str) -> DiagnosisTaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Diagnosis task not found: {task_id}")
        return task

    def list_tasks(self, limit: int = 20) -> list[DiagnosisTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diagnosis_tasks
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
                CREATE TABLE IF NOT EXISTS diagnosis_tasks (
                    task_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    service TEXT,
                    severity TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    trigger_metadata_json TEXT NOT NULL,
                    result_json TEXT,
                    incident_id TEXT,
                    diagnosis_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _task_values(task: DiagnosisTaskRecord) -> tuple[Any, ...]:
    return (
        task.task_id,
        task.source,
        task.status.value,
        task.question,
        task.session_id,
        task.service,
        task.severity.value,
        _json_dumps(task.labels),
        _json_dumps(task.trigger_metadata),
        _json_dumps(task.result.model_dump(mode="json")) if task.result else None,
        task.incident_id,
        task.diagnosis_id,
        task.error,
        _datetime_to_text(task.created_at),
        _datetime_to_text(task.updated_at),
        _datetime_to_text(task.started_at) if task.started_at else None,
        _datetime_to_text(task.finished_at) if task.finished_at else None,
    )


def _task_from_row(row: sqlite3.Row) -> DiagnosisTaskRecord:
    result_data = _json_loads(row["result_json"], None)
    return DiagnosisTaskRecord(
        task_id=row["task_id"],
        source=row["source"],
        status=DiagnosisTaskStatus(row["status"]),
        question=row["question"],
        session_id=row["session_id"],
        service=row["service"],
        severity=AlertSeverity(row["severity"]),
        labels=_json_loads(row["labels_json"], {}),
        trigger_metadata=_json_loads(row["trigger_metadata_json"], {}),
        result=ChatResponse.model_validate(result_data) if result_data else None,
        incident_id=row["incident_id"],
        diagnosis_id=row["diagnosis_id"],
        error=row["error"],
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
        started_at=_datetime_from_text(row["started_at"]) if row["started_at"] else None,
        finished_at=_datetime_from_text(row["finished_at"]) if row["finished_at"] else None,
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _datetime_to_text(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)

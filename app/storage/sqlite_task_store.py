import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.schemas import (
    AlertGroupRecord,
    AlertGroupStatus,
    AlertSeverity,
    ChatResponse,
    DiagnosisTaskEventRecord,
    DiagnosisTaskEventType,
    DiagnosisTaskRecord,
    DiagnosisTaskStatus,
    HumanReviewRequestRecord,
    HumanReviewStatus,
    OpsGraphCheckpointRecord,
)
from app.storage.database import (
    Database,
    DatabaseConnection,
    DatabaseRow,
    configured_database_target,
)


class SQLiteTaskStore:
    """Diagnosis task repository backed by the configured database."""

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
    def from_settings(cls) -> "SQLiteTaskStore":
        return cls(
            configured_database_target(settings.incident_db_path),
            auto_create_schema=settings.database_auto_create_schema,
        )

    def create_task(
        self,
        source: str,
        question: str,
        session_id: str,
        alert_group_id: str | None = None,
        rerun_of_task_id: str | None = None,
        resume_of_task_id: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        service: str | None = None,
        severity: AlertSeverity = AlertSeverity.warning,
        labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        task = DiagnosisTaskRecord(
            task_id=f"task_{uuid4().hex}",
            alert_group_id=alert_group_id,
            rerun_of_task_id=rerun_of_task_id,
            resume_of_task_id=resume_of_task_id,
            thread_id=thread_id or f"thread_{uuid4().hex}",
            run_id=run_id or f"run_{uuid4().hex}",
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
                    task_id, alert_group_id, rerun_of_task_id, resume_of_task_id,
                    thread_id, run_id, source, status, question, session_id, service, severity,
                    labels_json, trigger_metadata_json, result_json, incident_id,
                    diagnosis_id, error, created_at, updated_at, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _task_values(task),
            )

        self.append_event(
            task_id=task.task_id,
            event_type=DiagnosisTaskEventType.queued,
            message="Diagnosis task accepted.",
            data={
                "source": source,
                "service": service,
                "severity": severity.value,
            },
        )
        return task

    def upsert_alert_group(
        self,
        dedupe_key: str,
        source: str,
        title: str,
        service: str | None = None,
        severity: AlertSeverity = AlertSeverity.warning,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
    ) -> AlertGroupRecord:
        now = datetime.utcnow()
        existing = self.get_alert_group_by_dedupe_key(dedupe_key)
        if existing is not None:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE alert_groups
                    SET source = ?, title = ?, service = ?, severity = ?, status = ?,
                        labels_json = ?, annotations_json = ?,
                        trigger_count = trigger_count + 1,
                        updated_at = ?, last_seen_at = ?
                    WHERE group_id = ?
                    """,
                    (
                        source,
                        title,
                        service,
                        severity.value,
                        AlertGroupStatus.active.value,
                        _json_dumps(labels or {}),
                        _json_dumps(annotations or {}),
                        _datetime_to_text(now),
                        _datetime_to_text(now),
                        existing.group_id,
                    ),
                )
            return self.require_alert_group(existing.group_id)

        group = AlertGroupRecord(
            group_id=f"ag_{uuid4().hex}",
            dedupe_key=dedupe_key,
            source=source,
            title=title,
            service=service,
            severity=severity,
            status=AlertGroupStatus.active,
            labels=labels or {},
            annotations=annotations or {},
            trigger_count=1,
            created_at=now,
            updated_at=now,
            first_seen_at=now,
            last_seen_at=now,
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_groups (
                    group_id, dedupe_key, source, title, service, severity, status,
                    labels_json, annotations_json, trigger_count, latest_task_id,
                    incident_id, diagnosis_id, created_at, updated_at, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _alert_group_values(group),
            )

        return group

    def attach_task_to_alert_group(self, group_id: str, task_id: str) -> AlertGroupRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE alert_groups
                SET latest_task_id = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (task_id, _datetime_to_text(now), group_id),
            )
        return self.require_alert_group(group_id)

    def mark_alert_group_diagnosed(
        self,
        group_id: str,
        response: ChatResponse,
    ) -> AlertGroupRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE alert_groups
                SET incident_id = ?, diagnosis_id = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (
                    response.metadata.get("incident_id"),
                    response.metadata.get("diagnosis_id"),
                    _datetime_to_text(now),
                    group_id,
                ),
            )
        return self.require_alert_group(group_id)

    def resolve_alert_group(self, dedupe_key: str) -> AlertGroupRecord | None:
        group = self.get_alert_group_by_dedupe_key(dedupe_key)
        if group is None:
            return None

        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE alert_groups
                SET status = ?, trigger_count = trigger_count + 1,
                    updated_at = ?, last_seen_at = ?
                WHERE group_id = ?
                """,
                (
                    AlertGroupStatus.resolved.value,
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    group.group_id,
                ),
            )
        return self.require_alert_group(group.group_id)

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
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.running,
            message="Diagnosis task started.",
        )
        return self.require_task(task_id)

    def mark_cancel_requested(
        self,
        task_id: str,
        requested_by: str,
        reason: str | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.cancel_requested.value,
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.cancel_requested,
            message="Diagnosis task cancellation requested.",
            data={
                "requested_by": requested_by,
                "reason": reason,
            },
        )
        return self.require_task(task_id)

    def mark_canceled(
        self,
        task_id: str,
        requested_by: str = "system",
        reason: str | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, updated_at = ?, finished_at = ?
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.canceled.value,
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.canceled,
            message="Diagnosis task canceled.",
            data={
                "requested_by": requested_by,
                "reason": reason,
            },
        )
        return self.require_task(task_id)

    def mark_timed_out(
        self,
        task_id: str,
        requested_by: str = "system",
        reason: str | None = None,
        max_age_seconds: int | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        error = reason or "Diagnosis task exceeded its execution timeout."
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, error = ?, updated_at = ?, finished_at = ?
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.timed_out.value,
                    error[:4000],
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.timed_out,
            message="Diagnosis task timed out.",
            data={
                "requested_by": requested_by,
                "reason": reason,
                "max_age_seconds": max_age_seconds,
            },
        )
        return self.require_task(task_id)

    def mark_waiting_review(
        self,
        task_id: str,
        response: ChatResponse,
        review_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_tasks
                SET status = ?, result_json = ?, incident_id = ?, diagnosis_id = ?,
                    error = NULL, updated_at = ?, finished_at = NULL
                WHERE task_id = ?
                """,
                (
                    DiagnosisTaskStatus.waiting_review.value,
                    _json_dumps(response.model_dump(mode="json")),
                    response.metadata.get("incident_id"),
                    response.metadata.get("diagnosis_id"),
                    _datetime_to_text(now),
                    task_id,
                ),
            )
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.waiting_review,
            message="Diagnosis task is waiting for human review.",
            data={
                "review_ids": review_ids or [],
                "reason": reason,
            },
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
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.succeeded,
            message="Diagnosis task completed successfully.",
            data={
                "incident_id": response.metadata.get("incident_id"),
                "diagnosis_id": response.metadata.get("diagnosis_id"),
            },
        )
        return self.require_task(task_id)

    def mark_failed(
        self,
        task_id: str,
        error: str,
        response: ChatResponse | None = None,
    ) -> DiagnosisTaskRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            if response is None:
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
            else:
                connection.execute(
                    """
                    UPDATE diagnosis_tasks
                    SET status = ?, result_json = ?, incident_id = ?, diagnosis_id = ?,
                        error = ?, updated_at = ?, finished_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        DiagnosisTaskStatus.failed.value,
                        _json_dumps(response.model_dump(mode="json")),
                        response.metadata.get("incident_id"),
                        response.metadata.get("diagnosis_id"),
                        error[:4000],
                        _datetime_to_text(now),
                        _datetime_to_text(now),
                        task_id,
                    ),
                )
        self.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.failed,
            message="Diagnosis task failed.",
            data={"error": error[:4000]},
        )
        return self.require_task(task_id)

    def append_event(
        self,
        task_id: str,
        event_type: DiagnosisTaskEventType,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> DiagnosisTaskEventRecord:
        event = DiagnosisTaskEventRecord(
            event_id=f"event_{uuid4().hex}",
            task_id=task_id,
            event_type=event_type,
            message=message,
            data=data or {},
            created_at=datetime.utcnow(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnosis_task_events (
                    event_id, task_id, event_type, message, data_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                _event_values(event),
            )
        return event

    def save_graph_checkpoint(
        self,
        task_id: str,
        node_name: str,
        status: str,
        state: dict[str, Any],
        error: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> OpsGraphCheckpointRecord:
        checkpoint = OpsGraphCheckpointRecord(
            checkpoint_id=f"chk_{uuid4().hex}",
            task_id=task_id,
            thread_id=thread_id,
            run_id=run_id,
            node_name=node_name,
            status=status,
            state=state,
            error=error[:4000] if error else None,
            created_at=datetime.utcnow(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ops_graph_checkpoints (
                    checkpoint_id, task_id, thread_id, run_id, node_name, status,
                    state_json, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _checkpoint_values(checkpoint),
            )
        return checkpoint

    def create_human_review_request(
        self,
        task_id: str,
        service: str | None,
        proposed_actions: list[str],
        risk_reasons: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> HumanReviewRequestRecord:
        review = HumanReviewRequestRecord(
            review_id=f"review_{uuid4().hex}",
            task_id=task_id,
            service=service,
            proposed_actions=proposed_actions,
            risk_reasons=risk_reasons,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO human_review_requests (
                    review_id, task_id, service, status, proposed_actions_json,
                    risk_reasons_json, metadata_json, reviewer, decision_reason,
                    created_at, decided_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _human_review_values(review),
            )
        return review

    def get_human_review_request(self, review_id: str) -> HumanReviewRequestRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM human_review_requests WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        return _human_review_from_row(row) if row else None

    def get_graph_checkpoint(self, checkpoint_id: str) -> OpsGraphCheckpointRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ops_graph_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        return _checkpoint_from_row(row) if row else None

    def require_human_review_request(self, review_id: str) -> HumanReviewRequestRecord:
        review = self.get_human_review_request(review_id)
        if review is None:
            raise KeyError(f"Human review request not found: {review_id}")
        return review

    def list_human_review_requests(
        self,
        status: HumanReviewStatus | None = None,
        limit: int = 20,
    ) -> list[HumanReviewRequestRecord]:
        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT * FROM human_review_requests
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM human_review_requests
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status.value, limit),
                ).fetchall()
        return [_human_review_from_row(row) for row in rows]

    def list_human_review_requests_for_task(self, task_id: str) -> list[HumanReviewRequestRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM human_review_requests
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [_human_review_from_row(row) for row in rows]

    def decide_human_review_request(
        self,
        review_id: str,
        status: HumanReviewStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> HumanReviewRequestRecord:
        if status not in {HumanReviewStatus.approved, HumanReviewStatus.rejected}:
            raise ValueError("Human review decision must be approved or rejected.")

        review = self.require_human_review_request(review_id)
        decided_at = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE human_review_requests
                SET status = ?, reviewer = ?, decision_reason = ?, decided_at = ?
                WHERE review_id = ?
                """,
                (
                    status.value,
                    reviewer,
                    reason,
                    _datetime_to_text(decided_at),
                    review.review_id,
                ),
            )
        updated = self.require_human_review_request(review_id)
        event_type = (
            DiagnosisTaskEventType.human_review_approved
            if status == HumanReviewStatus.approved
            else DiagnosisTaskEventType.human_review_rejected
        )
        self.append_event(
            task_id=updated.task_id,
            event_type=event_type,
            message=f"Human review {status.value}.",
            data={
                "review_id": updated.review_id,
                "reviewer": reviewer,
                "reason": reason,
            },
        )
        return updated

    def get_task(self, task_id: str) -> DiagnosisTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnosis_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return _task_from_row(row) if row else None

    def get_alert_group(self, group_id: str) -> AlertGroupRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM alert_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return _alert_group_from_row(row) if row else None

    def require_alert_group(self, group_id: str) -> AlertGroupRecord:
        group = self.get_alert_group(group_id)
        if group is None:
            raise KeyError(f"Alert group not found: {group_id}")
        return group

    def get_alert_group_by_dedupe_key(self, dedupe_key: str) -> AlertGroupRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM alert_groups WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        return _alert_group_from_row(row) if row else None

    def require_task(self, task_id: str) -> DiagnosisTaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Diagnosis task not found: {task_id}")
        return task

    def is_cancel_requested(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        return bool(
            task
            and task.status
            in {
                DiagnosisTaskStatus.cancel_requested,
                DiagnosisTaskStatus.canceled,
            }
        )

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

    def list_task_reruns(self, task_id: str) -> list[DiagnosisTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diagnosis_tasks
                WHERE rerun_of_task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_task_resumes(self, task_id: str) -> list[DiagnosisTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diagnosis_tasks
                WHERE resume_of_task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_stale_active_tasks(
        self,
        max_age_seconds: int,
        limit: int = 50,
    ) -> list[DiagnosisTaskRecord]:
        cutoff = datetime.utcnow() - timedelta(seconds=max_age_seconds)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diagnosis_tasks
                WHERE status IN (?, ?)
                  AND COALESCE(started_at, updated_at, created_at) <= ?
                ORDER BY COALESCE(started_at, updated_at, created_at) ASC
                LIMIT ?
                """,
                (
                    DiagnosisTaskStatus.running.value,
                    DiagnosisTaskStatus.cancel_requested.value,
                    _datetime_to_text(cutoff),
                    limit,
                ),
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_alert_groups(self, limit: int = 20) -> list[AlertGroupRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM alert_groups
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_alert_group_from_row(row) for row in rows]

    def list_events(self, task_id: str) -> list[DiagnosisTaskEventRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diagnosis_task_events
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_graph_checkpoints(self, task_id: str) -> list[OpsGraphCheckpointRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ops_graph_checkpoints
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [_checkpoint_from_row(row) for row in rows]

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_tasks (
                    task_id TEXT PRIMARY KEY,
                    alert_group_id TEXT,
                    rerun_of_task_id TEXT,
                    resume_of_task_id TEXT,
                    thread_id TEXT,
                    run_id TEXT,
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
            _ensure_column(connection, "diagnosis_tasks", "alert_group_id", "TEXT")
            _ensure_column(connection, "diagnosis_tasks", "rerun_of_task_id", "TEXT")
            _ensure_column(connection, "diagnosis_tasks", "resume_of_task_id", "TEXT")
            _ensure_column(connection, "diagnosis_tasks", "thread_id", "TEXT")
            _ensure_column(connection, "diagnosis_tasks", "run_id", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnosis_tasks_rerun_of
                ON diagnosis_tasks (rerun_of_task_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnosis_tasks_resume_of
                ON diagnosis_tasks (resume_of_task_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnosis_tasks_thread_created
                ON diagnosis_tasks (thread_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnosis_tasks_status_updated
                ON diagnosis_tasks (status, updated_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_groups (
                    group_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    service TEXT,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    annotations_json TEXT NOT NULL,
                    trigger_count INTEGER NOT NULL,
                    latest_task_id TEXT,
                    incident_id TEXT,
                    diagnosis_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_task_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES diagnosis_tasks (task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ops_graph_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    thread_id TEXT,
                    run_id TEXT,
                    node_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES diagnosis_tasks (task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ops_graph_checkpoints_task_created
                ON ops_graph_checkpoints (task_id, created_at)
                """
            )
            _ensure_column(connection, "ops_graph_checkpoints", "thread_id", "TEXT")
            _ensure_column(connection, "ops_graph_checkpoints", "run_id", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ops_graph_checkpoints_thread_run
                ON ops_graph_checkpoints (thread_id, run_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS human_review_requests (
                    review_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    service TEXT,
                    status TEXT NOT NULL,
                    proposed_actions_json TEXT NOT NULL,
                    risk_reasons_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    reviewer TEXT,
                    decision_reason TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY (task_id) REFERENCES diagnosis_tasks (task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_human_review_requests_status_created
                ON human_review_requests (status, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_human_review_requests_task_created
                ON human_review_requests (task_id, created_at)
                """
            )

    def _connect(self) -> DatabaseConnection:
        return self.database.connect()


def _task_values(task: DiagnosisTaskRecord) -> tuple[Any, ...]:
    return (
        task.task_id,
        task.alert_group_id,
        task.rerun_of_task_id,
        task.resume_of_task_id,
        task.thread_id,
        task.run_id,
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


def _alert_group_values(group: AlertGroupRecord) -> tuple[Any, ...]:
    return (
        group.group_id,
        group.dedupe_key,
        group.source,
        group.title,
        group.service,
        group.severity.value,
        group.status.value,
        _json_dumps(group.labels),
        _json_dumps(group.annotations),
        group.trigger_count,
        group.latest_task_id,
        group.incident_id,
        group.diagnosis_id,
        _datetime_to_text(group.created_at),
        _datetime_to_text(group.updated_at),
        _datetime_to_text(group.first_seen_at),
        _datetime_to_text(group.last_seen_at),
    )


def _event_values(event: DiagnosisTaskEventRecord) -> tuple[Any, ...]:
    return (
        event.event_id,
        event.task_id,
        event.event_type.value,
        event.message,
        _json_dumps(event.data),
        _datetime_to_text(event.created_at),
    )


def _checkpoint_values(checkpoint: OpsGraphCheckpointRecord) -> tuple[Any, ...]:
    return (
        checkpoint.checkpoint_id,
        checkpoint.task_id,
        checkpoint.thread_id,
        checkpoint.run_id,
        checkpoint.node_name,
        checkpoint.status,
        _json_dumps(checkpoint.state),
        checkpoint.error,
        _datetime_to_text(checkpoint.created_at),
    )


def _human_review_values(review: HumanReviewRequestRecord) -> tuple[Any, ...]:
    return (
        review.review_id,
        review.task_id,
        review.service,
        review.status.value,
        _json_dumps(review.proposed_actions),
        _json_dumps(review.risk_reasons),
        _json_dumps(review.metadata),
        review.reviewer,
        review.decision_reason,
        _datetime_to_text(review.created_at),
        _datetime_to_text(review.decided_at) if review.decided_at else None,
    )


def _task_from_row(row: DatabaseRow) -> DiagnosisTaskRecord:
    result_data = _json_loads(row["result_json"], None)
    return DiagnosisTaskRecord(
        task_id=row["task_id"],
        alert_group_id=row["alert_group_id"],
        rerun_of_task_id=row["rerun_of_task_id"],
        resume_of_task_id=row["resume_of_task_id"],
        thread_id=row["thread_id"],
        run_id=row["run_id"],
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


def _alert_group_from_row(row: DatabaseRow) -> AlertGroupRecord:
    return AlertGroupRecord(
        group_id=row["group_id"],
        dedupe_key=row["dedupe_key"],
        source=row["source"],
        title=row["title"],
        service=row["service"],
        severity=AlertSeverity(row["severity"]),
        status=AlertGroupStatus(row["status"]),
        labels=_json_loads(row["labels_json"], {}),
        annotations=_json_loads(row["annotations_json"], {}),
        trigger_count=row["trigger_count"],
        latest_task_id=row["latest_task_id"],
        incident_id=row["incident_id"],
        diagnosis_id=row["diagnosis_id"],
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
        first_seen_at=_datetime_from_text(row["first_seen_at"]),
        last_seen_at=_datetime_from_text(row["last_seen_at"]),
    )


def _event_from_row(row: DatabaseRow) -> DiagnosisTaskEventRecord:
    return DiagnosisTaskEventRecord(
        event_id=row["event_id"],
        task_id=row["task_id"],
        event_type=DiagnosisTaskEventType(row["event_type"]),
        message=row["message"],
        data=_json_loads(row["data_json"], {}),
        created_at=_datetime_from_text(row["created_at"]),
    )


def _checkpoint_from_row(row: DatabaseRow) -> OpsGraphCheckpointRecord:
    return OpsGraphCheckpointRecord(
        checkpoint_id=row["checkpoint_id"],
        task_id=row["task_id"],
        thread_id=row["thread_id"],
        run_id=row["run_id"],
        node_name=row["node_name"],
        status=row["status"],
        state=_json_loads(row["state_json"], {}),
        error=row["error"],
        created_at=_datetime_from_text(row["created_at"]),
    )


def _human_review_from_row(row: DatabaseRow) -> HumanReviewRequestRecord:
    return HumanReviewRequestRecord(
        review_id=row["review_id"],
        task_id=row["task_id"],
        service=row["service"],
        status=HumanReviewStatus(row["status"]),
        proposed_actions=_json_loads(row["proposed_actions_json"], []),
        risk_reasons=_json_loads(row["risk_reasons_json"], []),
        metadata=_json_loads(row["metadata_json"], {}),
        reviewer=row["reviewer"],
        decision_reason=row["decision_reason"],
        created_at=_datetime_from_text(row["created_at"]),
        decided_at=_datetime_from_text(row["decided_at"]) if row["decided_at"] else None,
    )


def _ensure_column(
    connection: DatabaseConnection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = connection.column_names(table_name)
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


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

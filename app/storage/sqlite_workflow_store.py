from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationRecord,
    WorkflowApplicationStatus,
    WorkflowApplicationUpdate,
    WorkflowDraftRecord,
    WorkflowDraftRunResponse,
    WorkflowExecutionSource,
    WorkflowGraphDefinition,
    WorkflowAuditEventRecord,
    WorkflowReviewRequestRecord,
    WorkflowReviewStatus,
    WorkflowRunEventRecord,
    WorkflowRunEventType,
    WorkflowRunMetricsResponse,
    WorkflowRunRecord,
    WorkflowRunStatus,
    WorkflowVersionRecord,
)


class WorkflowRevisionConflict(ValueError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"Draft revision conflict: expected {expected_revision}, current {current_revision}."
        )


class WorkflowRunStateConflict(ValueError):
    pass


class WorkflowReviewConflict(ValueError):
    pass


class SQLiteWorkflowStore:
    """Persistent control-plane state for workflow applications and versions."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @classmethod
    def from_settings(cls) -> "SQLiteWorkflowStore":
        return cls(settings.workflow_db_path)

    def create_application(
        self,
        request: WorkflowApplicationCreate,
    ) -> tuple[WorkflowApplicationRecord, WorkflowDraftRecord]:
        now = datetime.utcnow()
        application = WorkflowApplicationRecord(
            app_id=f"wfapp_{uuid4().hex}",
            name=request.name,
            description=request.description,
            created_at=now,
            updated_at=now,
        )
        draft = WorkflowDraftRecord(
            draft_id=f"wfdraft_{uuid4().hex}",
            app_id=application.app_id,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_applications (
                    app_id, name, description, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    application.app_id,
                    application.name,
                    application.description,
                    application.status.value,
                    application.created_at.isoformat(),
                    application.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_drafts (
                    draft_id, app_id, revision, graph_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.app_id,
                    draft.revision,
                    draft.graph.model_dump_json(),
                    draft.created_at.isoformat(),
                    draft.updated_at.isoformat(),
                ),
            )
        return application, draft

    def get_application(self, app_id: str) -> WorkflowApplicationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_applications WHERE app_id = ?",
                (app_id,),
            ).fetchone()
        return _application_from_row(row) if row is not None else None

    def require_application(self, app_id: str) -> WorkflowApplicationRecord:
        application = self.get_application(app_id)
        if application is None:
            raise KeyError(app_id)
        return application

    def list_applications(
        self,
        limit: int = 20,
        include_archived: bool = False,
    ) -> list[WorkflowApplicationRecord]:
        where_clause = "" if include_archived else "WHERE status != ?"
        parameters = () if include_archived else (WorkflowApplicationStatus.archived.value,)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM workflow_applications
                {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return [_application_from_row(row) for row in rows]

    def update_application(
        self,
        app_id: str,
        request: WorkflowApplicationUpdate,
    ) -> WorkflowApplicationRecord:
        current = self.require_application(app_id)
        updated = current.model_copy(
            update={
                "name": request.name if request.name is not None else current.name,
                "description": (
                    request.description
                    if request.description is not None
                    else current.description
                ),
                "status": request.status or current.status,
                "updated_at": datetime.utcnow(),
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workflow_applications
                SET name = ?, description = ?, status = ?, updated_at = ?
                WHERE app_id = ?
                """,
                (
                    updated.name,
                    updated.description,
                    updated.status.value,
                    updated.updated_at.isoformat(),
                    app_id,
                ),
            )
        return updated

    def get_draft(self, app_id: str) -> WorkflowDraftRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_drafts WHERE app_id = ?",
                (app_id,),
            ).fetchone()
        return _draft_from_row(row) if row is not None else None

    def require_draft(self, app_id: str) -> WorkflowDraftRecord:
        draft = self.get_draft(app_id)
        if draft is None:
            raise KeyError(app_id)
        return draft

    def update_draft(
        self,
        app_id: str,
        expected_revision: int,
        graph: WorkflowGraphDefinition,
    ) -> WorkflowDraftRecord:
        self.require_application(app_id)
        now = datetime.utcnow().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_drafts
                SET revision = revision + 1, graph_json = ?, updated_at = ?
                WHERE app_id = ? AND revision = ?
                """,
                (graph.model_dump_json(), now, app_id, expected_revision),
            )
        if cursor.rowcount == 0:
            current = self.require_draft(app_id)
            raise WorkflowRevisionConflict(expected_revision, current.revision)
        return self.require_draft(app_id)

    def publish_draft(
        self,
        app_id: str,
        expected_revision: int,
        published_by: str,
        release_notes: str = "",
    ) -> WorkflowVersionRecord:
        now = datetime.utcnow()
        version_id = f"wfver_{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft_row = connection.execute(
                "SELECT * FROM workflow_drafts WHERE app_id = ?",
                (app_id,),
            ).fetchone()
            if draft_row is None:
                raise KeyError(app_id)

            draft = _draft_from_row(draft_row)
            if draft.revision != expected_revision:
                raise WorkflowRevisionConflict(expected_revision, draft.revision)

            graph_json = _canonical_graph_json(draft.graph)
            graph_sha256 = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
            existing_row = connection.execute(
                """
                SELECT * FROM workflow_versions
                WHERE app_id = ? AND source_draft_revision = ?
                """,
                (app_id, draft.revision),
            ).fetchone()
            if existing_row is not None:
                existing = _version_from_row(existing_row)
                if existing.graph_sha256 != graph_sha256:
                    raise RuntimeError(
                        "Published workflow revision has inconsistent content."
                    )
                return existing

            next_version = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM workflow_versions
                    WHERE app_id = ?
                    """,
                    (app_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO workflow_versions (
                    version_id, app_id, version_number, source_draft_revision,
                    graph_json, graph_sha256, release_notes, published_by, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    app_id,
                    next_version,
                    draft.revision,
                    graph_json,
                    graph_sha256,
                    release_notes,
                    published_by,
                    now.isoformat(),
                ),
            )
            _insert_audit_event(
                connection=connection,
                app_id=app_id,
                actor=published_by,
                action="workflow.version_published",
                resource_type="workflow_version",
                resource_id=version_id,
                details={
                    "version_number": next_version,
                    "source_draft_revision": draft.revision,
                    "graph_sha256": graph_sha256,
                    "release_notes": release_notes,
                },
            )
        version = self.get_version(app_id, next_version)
        if version is None:
            raise RuntimeError("Published workflow version could not be reloaded.")
        return version

    def list_versions(
        self,
        app_id: str,
        limit: int = 20,
    ) -> list[WorkflowVersionRecord]:
        self.require_application(app_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_versions
                WHERE app_id = ?
                ORDER BY version_number DESC
                LIMIT ?
                """,
                (app_id, limit),
            ).fetchall()
        return [_version_from_row(row) for row in rows]

    def get_version(
        self,
        app_id: str,
        version_number: int,
    ) -> WorkflowVersionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_versions
                WHERE app_id = ? AND version_number = ?
                """,
                (app_id, version_number),
            ).fetchone()
        return _version_from_row(row) if row is not None else None

    def restore_version_to_draft(
        self,
        app_id: str,
        version_number: int,
        expected_revision: int,
        requested_by: str = "manual",
        reason: str = "",
    ) -> tuple[WorkflowVersionRecord, WorkflowDraftRecord]:
        now = datetime.utcnow().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version_row = connection.execute(
                """
                SELECT * FROM workflow_versions
                WHERE app_id = ? AND version_number = ?
                """,
                (app_id, version_number),
            ).fetchone()
            if version_row is None:
                raise KeyError((app_id, version_number))

            draft_row = connection.execute(
                "SELECT * FROM workflow_drafts WHERE app_id = ?",
                (app_id,),
            ).fetchone()
            if draft_row is None:
                raise KeyError(app_id)
            current_revision = int(draft_row["revision"])
            if current_revision != expected_revision:
                raise WorkflowRevisionConflict(expected_revision, current_revision)

            connection.execute(
                """
                UPDATE workflow_drafts
                SET revision = revision + 1, graph_json = ?, updated_at = ?
                WHERE app_id = ? AND revision = ?
                """,
                (
                    version_row["graph_json"],
                    now,
                    app_id,
                    expected_revision,
                ),
            )
            _insert_audit_event(
                connection=connection,
                app_id=app_id,
                actor=requested_by,
                action="workflow.version_restored_to_draft",
                resource_type="workflow_version",
                resource_id=version_row["version_id"],
                details={
                    "version_number": version_number,
                    "previous_draft_revision": expected_revision,
                    "new_draft_revision": expected_revision + 1,
                    "reason": reason,
                },
            )

        restored_draft = self.require_draft(app_id)
        return _version_from_row(version_row), restored_draft

    def create_run(
        self,
        app_id: str,
        execution_source: WorkflowExecutionSource,
        draft_revision: int,
        version_number: int | None,
        thread_id: str,
        inputs: dict[str, Any],
        started_by: str,
        graph: WorkflowGraphDefinition,
    ) -> WorkflowRunRecord:
        self.require_application(app_id)
        now = datetime.utcnow()
        graph_json = _canonical_graph_json(graph)
        run = WorkflowRunRecord(
            run_id=f"wfrun_{uuid4().hex}",
            app_id=app_id,
            execution_source=execution_source,
            draft_revision=draft_revision,
            version_number=version_number,
            thread_id=thread_id,
            status=WorkflowRunStatus.running,
            inputs=inputs,
            started_by=started_by,
            graph_sha256=hashlib.sha256(graph_json.encode("utf-8")).hexdigest(),
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, app_id, execution_source, draft_revision,
                    version_number, thread_id, status, inputs_json, output_json,
                    error, started_by, graph_json, graph_sha256, created_at,
                    updated_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    run.run_id,
                    run.app_id,
                    run.execution_source.value,
                    run.draft_revision,
                    run.version_number,
                    run.thread_id,
                    run.status.value,
                    _json_dumps(run.inputs),
                    None,
                    run.started_by,
                    graph_json,
                    run.graph_sha256,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            _insert_run_event(
                connection=connection,
                run_id=run.run_id,
                event_type=WorkflowRunEventType.run_started,
                message="Workflow run started.",
                data={
                    "execution_source": execution_source.value,
                    "draft_revision": draft_revision,
                    "version_number": version_number,
                    "thread_id": thread_id,
                },
            )
            _insert_audit_event(
                connection=connection,
                app_id=app_id,
                actor=started_by,
                action="workflow.run_started",
                resource_type="workflow_run",
                resource_id=run.run_id,
                details={
                    "execution_source": execution_source.value,
                    "version_number": version_number,
                    "thread_id": thread_id,
                },
            )
        return run

    def get_run(self, app_id: str, run_id: str) -> WorkflowRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_runs
                WHERE app_id = ? AND run_id = ?
                """,
                (app_id, run_id),
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def require_run(self, app_id: str, run_id: str) -> WorkflowRunRecord:
        run = self.get_run(app_id, run_id)
        if run is None:
            raise KeyError((app_id, run_id))
        return run

    def list_runs(
        self,
        app_id: str,
        status: WorkflowRunStatus | None = None,
        limit: int = 20,
    ) -> list[WorkflowRunRecord]:
        self.require_application(app_id)
        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT * FROM workflow_runs
                    WHERE app_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (app_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM workflow_runs
                    WHERE app_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (app_id, status.value, limit),
                ).fetchall()
        return [_run_from_row(row) for row in rows]

    def get_run_graph(self, app_id: str, run_id: str) -> WorkflowGraphDefinition:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT graph_json FROM workflow_runs
                WHERE app_id = ? AND run_id = ?
                """,
                (app_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError((app_id, run_id))
        return WorkflowGraphDefinition.model_validate(json.loads(row["graph_json"]))

    def append_run_event(
        self,
        run_id: str,
        event_type: WorkflowRunEventType,
        message: str,
        node_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> WorkflowRunEventRecord:
        with self._connect() as connection:
            return _insert_run_event(
                connection=connection,
                run_id=run_id,
                event_type=event_type,
                message=message,
                node_id=node_id,
                data=data,
            )

    def list_run_events(
        self,
        app_id: str,
        run_id: str,
    ) -> list[WorkflowRunEventRecord]:
        self.require_run(app_id, run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_run_events
                WHERE run_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (run_id,),
            ).fetchall()
        return [_run_event_from_row(row) for row in rows]

    def complete_run(
        self,
        app_id: str,
        run_id: str,
        result: WorkflowDraftRunResponse,
    ) -> WorkflowRunRecord:
        if result.status not in {
            WorkflowRunStatus.succeeded,
            WorkflowRunStatus.waiting_review,
        }:
            raise ValueError(f"Unsupported workflow result status: {result.status}")
        now = datetime.utcnow()
        finished_at = now if result.status == WorkflowRunStatus.succeeded else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, output_json = ?, error = NULL,
                    updated_at = ?, finished_at = ?
                WHERE app_id = ? AND run_id = ?
                  AND status IN (?, ?)
                """,
                (
                    result.status.value,
                    _json_dumps(result.output),
                    now.isoformat(),
                    finished_at.isoformat() if finished_at else None,
                    app_id,
                    run_id,
                    WorkflowRunStatus.running.value,
                    WorkflowRunStatus.waiting_review.value,
                ),
            )
            if cursor.rowcount == 0:
                current_row = connection.execute(
                    """
                    SELECT * FROM workflow_runs
                    WHERE app_id = ? AND run_id = ?
                    """,
                    (app_id, run_id),
                ).fetchone()
                if current_row is None:
                    raise KeyError((app_id, run_id))
                current = _run_from_row(current_row)
                raise WorkflowRunStateConflict(
                    f"Workflow run {run_id} cannot transition from {current.status.value} "
                    f"to {result.status.value}."
                )
            if result.status == WorkflowRunStatus.succeeded:
                _insert_run_event(
                    connection=connection,
                    run_id=run_id,
                    event_type=WorkflowRunEventType.run_succeeded,
                    message="Workflow run succeeded.",
                    data={"trace": result.trace},
                )
        return self.require_run(app_id, run_id)

    def fail_run(
        self,
        app_id: str,
        run_id: str,
        error: str,
    ) -> WorkflowRunRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, error = ?, updated_at = ?, finished_at = ?
                WHERE app_id = ? AND run_id = ?
                  AND status IN (?, ?)
                """,
                (
                    WorkflowRunStatus.failed.value,
                    error[:4000],
                    now.isoformat(),
                    now.isoformat(),
                    app_id,
                    run_id,
                    WorkflowRunStatus.running.value,
                    WorkflowRunStatus.waiting_review.value,
                ),
            )
            if cursor.rowcount:
                _insert_run_event(
                    connection=connection,
                    run_id=run_id,
                    event_type=WorkflowRunEventType.run_failed,
                    message="Workflow run failed.",
                    data={"error": error[:4000]},
                )
        return self.require_run(app_id, run_id)

    def create_review_request(
        self,
        app_id: str,
        run_id: str,
        node_id: str,
        payload: dict[str, Any],
    ) -> WorkflowReviewRequestRecord:
        self.require_run(app_id, run_id)
        now = datetime.utcnow()
        review = WorkflowReviewRequestRecord(
            review_id=f"wfreview_{uuid4().hex}",
            run_id=run_id,
            node_id=node_id,
            payload=payload,
            created_at=now,
        )
        with self._connect() as connection:
            existing_row = connection.execute(
                """
                SELECT * FROM workflow_review_requests
                WHERE run_id = ? AND node_id = ?
                """,
                (run_id, node_id),
            ).fetchone()
            if existing_row is not None:
                return _review_from_row(existing_row)
            connection.execute(
                """
                INSERT INTO workflow_review_requests (
                    review_id, run_id, node_id, status, payload_json,
                    reviewer, decision_reason, created_at, decided_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, NULL)
                """,
                (
                    review.review_id,
                    review.run_id,
                    review.node_id,
                    review.status.value,
                    _json_dumps(review.payload),
                    review.created_at.isoformat(),
                ),
            )
            _insert_run_event(
                connection=connection,
                run_id=run_id,
                event_type=WorkflowRunEventType.review_requested,
                message="Workflow run requires human review.",
                node_id=node_id,
                data={"review_id": review.review_id, "payload": payload},
            )
        return review

    def get_review(
        self,
        app_id: str,
        run_id: str,
        review_id: str,
    ) -> WorkflowReviewRequestRecord | None:
        self.require_run(app_id, run_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_review_requests
                WHERE run_id = ? AND review_id = ?
                """,
                (run_id, review_id),
            ).fetchone()
        return _review_from_row(row) if row is not None else None

    def list_reviews(
        self,
        app_id: str,
        run_id: str,
    ) -> list[WorkflowReviewRequestRecord]:
        self.require_run(app_id, run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_review_requests
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (run_id,),
            ).fetchall()
        return [_review_from_row(row) for row in rows]

    def decide_review(
        self,
        app_id: str,
        run_id: str,
        review_id: str,
        decision: WorkflowReviewStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> WorkflowReviewRequestRecord:
        if decision not in {
            WorkflowReviewStatus.approved,
            WorkflowReviewStatus.rejected,
        }:
            raise ValueError("Workflow review decision must be approved or rejected.")
        run = self.require_run(app_id, run_id)
        if run.status != WorkflowRunStatus.waiting_review:
            raise WorkflowRunStateConflict(
                f"Workflow run {run_id} is {run.status.value}, not waiting_review."
            )

        now = datetime.utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_review_requests
                SET status = ?, reviewer = ?, decision_reason = ?, decided_at = ?
                WHERE run_id = ? AND review_id = ? AND status = ?
                """,
                (
                    decision.value,
                    reviewer,
                    reason,
                    now.isoformat(),
                    run_id,
                    review_id,
                    WorkflowReviewStatus.pending.value,
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    """
                    SELECT * FROM workflow_review_requests
                    WHERE run_id = ? AND review_id = ?
                    """,
                    (run_id, review_id),
                ).fetchone()
                if row is None:
                    raise KeyError((run_id, review_id))
                raise WorkflowReviewConflict(
                    f"Workflow review {review_id} has already been decided."
                )

            event_type = (
                WorkflowRunEventType.review_approved
                if decision == WorkflowReviewStatus.approved
                else WorkflowRunEventType.review_rejected
            )
            _insert_run_event(
                connection=connection,
                run_id=run_id,
                event_type=event_type,
                message=f"Workflow review {decision.value}.",
                data={
                    "review_id": review_id,
                    "reviewer": reviewer,
                    "reason": reason,
                },
            )
            _insert_audit_event(
                connection=connection,
                app_id=app_id,
                actor=reviewer,
                action=f"workflow.review_{decision.value}",
                resource_type="workflow_review",
                resource_id=review_id,
                details={"run_id": run_id, "reason": reason},
            )
        review = self.get_review(app_id, run_id, review_id)
        if review is None:
            raise RuntimeError("Workflow review could not be reloaded.")
        return review

    def reject_run(
        self,
        app_id: str,
        run_id: str,
        reviewer: str,
        reason: str | None,
    ) -> WorkflowRunRecord:
        now = datetime.utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, error = ?, updated_at = ?, finished_at = ?
                WHERE app_id = ? AND run_id = ? AND status = ?
                """,
                (
                    WorkflowRunStatus.rejected.value,
                    reason,
                    now.isoformat(),
                    now.isoformat(),
                    app_id,
                    run_id,
                    WorkflowRunStatus.waiting_review.value,
                ),
            )
            if cursor.rowcount == 0:
                current_row = connection.execute(
                    """
                    SELECT * FROM workflow_runs
                    WHERE app_id = ? AND run_id = ?
                    """,
                    (app_id, run_id),
                ).fetchone()
                if current_row is None:
                    raise KeyError((app_id, run_id))
                current = _run_from_row(current_row)
                raise WorkflowRunStateConflict(
                    f"Workflow run {run_id} cannot be rejected from "
                    f"{current.status.value}."
                )
            _insert_run_event(
                connection=connection,
                run_id=run_id,
                event_type=WorkflowRunEventType.run_rejected,
                message="Workflow run rejected by human review.",
                data={"reviewer": reviewer, "reason": reason},
            )
            _insert_audit_event(
                connection=connection,
                app_id=app_id,
                actor=reviewer,
                action="workflow.run_rejected",
                resource_type="workflow_run",
                resource_id=run_id,
                details={"reason": reason},
            )
        return self.require_run(app_id, run_id)

    def append_audit_event(
        self,
        app_id: str,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
    ) -> WorkflowAuditEventRecord:
        self.require_application(app_id)
        with self._connect() as connection:
            return _insert_audit_event(
                connection=connection,
                app_id=app_id,
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
            )

    def list_audit_events(
        self,
        app_id: str,
        limit: int = 100,
    ) -> list[WorkflowAuditEventRecord]:
        self.require_application(app_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_audit_events
                WHERE app_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (app_id, limit),
            ).fetchall()
        return [_audit_event_from_row(row) for row in rows]

    def run_metrics(
        self,
        app_id: str,
        window_hours: int = 24,
    ) -> WorkflowRunMetricsResponse:
        self.require_application(app_id)
        since = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, created_at, finished_at
                FROM workflow_runs
                WHERE app_id = ? AND created_at >= ?
                """,
                (app_id, since),
            ).fetchall()
            pending_reviews = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM workflow_review_requests AS reviews
                    JOIN workflow_runs AS runs ON runs.run_id = reviews.run_id
                    WHERE runs.app_id = ? AND reviews.status = ?
                    """,
                    (app_id, WorkflowReviewStatus.pending.value),
                ).fetchone()[0]
            )

        by_status: dict[str, int] = {}
        durations: list[int] = []
        for row in rows:
            status_value = str(row["status"])
            by_status[status_value] = by_status.get(status_value, 0) + 1
            if row["finished_at"]:
                elapsed = (
                    datetime.fromisoformat(row["finished_at"])
                    - datetime.fromisoformat(row["created_at"])
                ).total_seconds()
                durations.append(max(0, int(elapsed * 1000)))
        total = len(rows)
        succeeded = by_status.get(WorkflowRunStatus.succeeded.value, 0)
        sorted_durations = sorted(durations)
        p95_index = max(0, int(len(sorted_durations) * 0.95 + 0.999) - 1)
        return WorkflowRunMetricsResponse(
            window_hours=window_hours,
            total_runs=total,
            by_status=by_status,
            success_rate=succeeded / total if total else 0.0,
            average_duration_ms=(
                sum(durations) / len(durations) if durations else None
            ),
            p95_duration_ms=(
                sorted_durations[p95_index] if sorted_durations else None
            ),
            pending_reviews=pending_reviews,
        )

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_applications (
                    app_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_drafts (
                    draft_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    graph_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (app_id) REFERENCES workflow_applications(app_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_versions (
                    version_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    source_draft_revision INTEGER NOT NULL,
                    graph_json TEXT NOT NULL,
                    graph_sha256 TEXT NOT NULL,
                    release_notes TEXT NOT NULL DEFAULT '',
                    published_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (app_id, version_number),
                    UNIQUE (app_id, source_draft_revision),
                    FOREIGN KEY (app_id) REFERENCES workflow_applications(app_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_applications_status_updated
                ON workflow_applications(status, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_versions_app_version
                ON workflow_versions(app_id, version_number DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    execution_source TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    version_number INTEGER,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    output_json TEXT,
                    error TEXT,
                    started_by TEXT NOT NULL,
                    graph_json TEXT NOT NULL,
                    graph_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY (app_id) REFERENCES workflow_applications(app_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_app_created
                ON workflow_runs(app_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_app_status
                ON workflow_runs(app_id, status, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_run_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    node_id TEXT,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_run_events_run_created
                ON workflow_run_events(run_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_review_requests (
                    review_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    reviewer TEXT,
                    decision_reason TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    UNIQUE (run_id, node_id),
                    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_reviews_run_status
                ON workflow_review_requests(run_id, status, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_audit_events (
                    audit_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (app_id) REFERENCES workflow_applications(app_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_audit_app_created
                ON workflow_audit_events(app_id, created_at DESC)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _application_from_row(row: sqlite3.Row) -> WorkflowApplicationRecord:
    return WorkflowApplicationRecord(
        app_id=row["app_id"],
        name=row["name"],
        description=row["description"],
        status=WorkflowApplicationStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _draft_from_row(row: sqlite3.Row) -> WorkflowDraftRecord:
    return WorkflowDraftRecord(
        draft_id=row["draft_id"],
        app_id=row["app_id"],
        revision=int(row["revision"]),
        graph=WorkflowGraphDefinition.model_validate(json.loads(row["graph_json"])),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _version_from_row(row: sqlite3.Row) -> WorkflowVersionRecord:
    return WorkflowVersionRecord(
        version_id=row["version_id"],
        app_id=row["app_id"],
        version_number=int(row["version_number"]),
        source_draft_revision=int(row["source_draft_revision"]),
        graph=WorkflowGraphDefinition.model_validate(json.loads(row["graph_json"])),
        graph_sha256=row["graph_sha256"],
        release_notes=row["release_notes"],
        published_by=row["published_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _canonical_graph_json(graph: WorkflowGraphDefinition) -> str:
    return json.dumps(
        graph.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _run_from_row(row: sqlite3.Row) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        run_id=row["run_id"],
        app_id=row["app_id"],
        execution_source=WorkflowExecutionSource(row["execution_source"]),
        draft_revision=int(row["draft_revision"]),
        version_number=(
            int(row["version_number"]) if row["version_number"] is not None else None
        ),
        thread_id=row["thread_id"],
        status=WorkflowRunStatus(row["status"]),
        inputs=json.loads(row["inputs_json"]),
        output=json.loads(row["output_json"]) if row["output_json"] is not None else None,
        error=row["error"],
        started_by=row["started_by"],
        graph_sha256=row["graph_sha256"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        finished_at=(
            datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
        ),
    )


def _insert_run_event(
    connection: sqlite3.Connection,
    run_id: str,
    event_type: WorkflowRunEventType,
    message: str,
    node_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> WorkflowRunEventRecord:
    event = WorkflowRunEventRecord(
        event_id=f"wfevent_{uuid4().hex}",
        run_id=run_id,
        event_type=event_type,
        message=message,
        node_id=node_id,
        data=data or {},
        created_at=datetime.utcnow(),
    )
    connection.execute(
        """
        INSERT INTO workflow_run_events (
            event_id, run_id, event_type, message, node_id, data_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.run_id,
            event.event_type.value,
            event.message,
            event.node_id,
            _json_dumps(event.data),
            event.created_at.isoformat(),
        ),
    )
    return event


def _run_event_from_row(row: sqlite3.Row) -> WorkflowRunEventRecord:
    return WorkflowRunEventRecord(
        event_id=row["event_id"],
        run_id=row["run_id"],
        event_type=WorkflowRunEventType(row["event_type"]),
        message=row["message"],
        node_id=row["node_id"],
        data=json.loads(row["data_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _review_from_row(row: sqlite3.Row) -> WorkflowReviewRequestRecord:
    return WorkflowReviewRequestRecord(
        review_id=row["review_id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        status=WorkflowReviewStatus(row["status"]),
        payload=json.loads(row["payload_json"]),
        reviewer=row["reviewer"],
        decision_reason=row["decision_reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
        decided_at=(
            datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None
        ),
    )


def _insert_audit_event(
    connection: sqlite3.Connection,
    app_id: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any] | None = None,
) -> WorkflowAuditEventRecord:
    event = WorkflowAuditEventRecord(
        audit_id=f"wfaudit_{uuid4().hex}",
        app_id=app_id,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        created_at=datetime.utcnow(),
    )
    connection.execute(
        """
        INSERT INTO workflow_audit_events (
            audit_id, app_id, actor, action, resource_type, resource_id,
            details_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.audit_id,
            event.app_id,
            event.actor,
            event.action,
            event.resource_type,
            event.resource_id,
            _json_dumps(event.details),
            event.created_at.isoformat(),
        ),
    )
    return event


def _audit_event_from_row(row: sqlite3.Row) -> WorkflowAuditEventRecord:
    return WorkflowAuditEventRecord(
        audit_id=row["audit_id"],
        app_id=row["app_id"],
        actor=row["actor"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        details=json.loads(row["details_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )

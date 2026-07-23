from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationRecord,
    WorkflowApplicationStatus,
    WorkflowApplicationUpdate,
    WorkflowDraftRecord,
    WorkflowGraphDefinition,
)


class WorkflowRevisionConflict(ValueError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"Draft revision conflict: expected {expected_revision}, current {current_revision}."
        )


class SQLiteWorkflowStore:
    """Persistent control-plane state for workflow applications and drafts."""

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
                CREATE INDEX IF NOT EXISTS idx_workflow_applications_status_updated
                ON workflow_applications(status, updated_at DESC)
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

from __future__ import annotations

import hashlib
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
    WorkflowVersionRecord,
)


class WorkflowRevisionConflict(ValueError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"Draft revision conflict: expected {expected_revision}, current {current_revision}."
        )


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

        restored_draft = self.require_draft(app_id)
        return _version_from_row(version_row), restored_draft

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

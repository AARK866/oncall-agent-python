import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.schemas import (
    AlertSeverity,
    ChatResponse,
    DiagnosisRecord,
    IncidentRecord,
    IncidentStatus,
    PlanTrace,
    ReactStep,
    SourceDocument,
    ToolResult,
)
from app.storage.database import (
    Database,
    DatabaseConnection,
    DatabaseRow,
    configured_database_target,
)


class SQLiteIncidentStore:
    """Incident repository backed by the configured SQLAlchemy database."""

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
    def from_settings(cls) -> "SQLiteIncidentStore":
        return cls(
            configured_database_target(settings.incident_db_path),
            auto_create_schema=settings.database_auto_create_schema,
        )

    def create_incident(
        self,
        title: str,
        service: str,
        question: str,
        session_id: str,
        severity: AlertSeverity = AlertSeverity.warning,
        status: IncidentStatus = IncidentStatus.investigating,
        labels: dict[str, str] | None = None,
    ) -> IncidentRecord:
        now = datetime.utcnow()
        incident = IncidentRecord(
            incident_id=f"inc_{uuid4().hex}",
            title=title,
            service=service,
            question=question,
            session_id=session_id,
            severity=severity,
            status=status,
            labels=labels or {},
            created_at=now,
            updated_at=now,
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, title, service, question, session_id, severity,
                    status, labels_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.incident_id,
                    incident.title,
                    incident.service,
                    incident.question,
                    incident.session_id,
                    incident.severity.value,
                    incident.status.value,
                    _json_dumps(incident.labels),
                    _datetime_to_text(incident.created_at),
                    _datetime_to_text(incident.updated_at),
                ),
            )

        return incident

    def save_diagnosis(self, incident_id: str, response: ChatResponse) -> DiagnosisRecord:
        now = datetime.utcnow()
        diagnosis = DiagnosisRecord(
            diagnosis_id=f"diag_{uuid4().hex}",
            incident_id=incident_id,
            answer=response.answer,
            mode=response.mode,
            sources=response.sources,
            tool_results=[
                ToolResult.model_validate(item)
                for item in response.metadata.get("tool_results", [])
            ],
            react_steps=[
                ReactStep.model_validate(item)
                for item in response.metadata.get("react_steps", [])
            ],
            plan_trace=_plan_trace_from_metadata(response.metadata.get("plan_trace")),
            metadata=response.metadata,
            created_at=now,
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnoses (
                    diagnosis_id, incident_id, answer, mode, sources_json,
                    tool_results_json, react_steps_json, plan_trace_json,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diagnosis.diagnosis_id,
                    diagnosis.incident_id,
                    diagnosis.answer,
                    diagnosis.mode.value,
                    _json_dumps([source.model_dump(mode="json") for source in diagnosis.sources]),
                    _json_dumps([result.model_dump(mode="json") for result in diagnosis.tool_results]),
                    _json_dumps([step.model_dump(mode="json") for step in diagnosis.react_steps]),
                    _json_dumps(
                        diagnosis.plan_trace.model_dump(mode="json")
                        if diagnosis.plan_trace
                        else None
                    ),
                    _json_dumps(diagnosis.metadata),
                    _datetime_to_text(diagnosis.created_at),
                ),
            )

        return diagnosis

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return _incident_from_row(row) if row else None

    def list_incidents(self, limit: int = 20) -> list[IncidentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM incidents
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_incident_from_row(row) for row in rows]

    def get_latest_diagnosis(self, incident_id: str) -> DiagnosisRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM diagnoses
                WHERE incident_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
        return _diagnosis_from_row(row) if row else None

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    service TEXT NOT NULL,
                    question TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    tool_results_json TEXT NOT NULL,
                    react_steps_json TEXT NOT NULL,
                    plan_trace_json TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (incident_id) REFERENCES incidents (incident_id)
                )
                """
            )

    def _connect(self) -> DatabaseConnection:
        return self.database.connect()


def _incident_from_row(row: DatabaseRow) -> IncidentRecord:
    return IncidentRecord(
        incident_id=row["incident_id"],
        title=row["title"],
        service=row["service"],
        question=row["question"],
        session_id=row["session_id"],
        severity=AlertSeverity(row["severity"]),
        status=IncidentStatus(row["status"]),
        labels=_json_loads(row["labels_json"], {}),
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def _diagnosis_from_row(row: DatabaseRow) -> DiagnosisRecord:
    plan_trace_data = _json_loads(row["plan_trace_json"], None)
    return DiagnosisRecord(
        diagnosis_id=row["diagnosis_id"],
        incident_id=row["incident_id"],
        answer=row["answer"],
        mode=row["mode"],
        sources=[
            SourceDocument.model_validate(item)
            for item in _json_loads(row["sources_json"], [])
        ],
        tool_results=[
            ToolResult.model_validate(item)
            for item in _json_loads(row["tool_results_json"], [])
        ],
        react_steps=[
            ReactStep.model_validate(item)
            for item in _json_loads(row["react_steps_json"], [])
        ],
        plan_trace=PlanTrace.model_validate(plan_trace_data) if plan_trace_data else None,
        metadata=_json_loads(row["metadata_json"], {}),
        created_at=_datetime_from_text(row["created_at"]),
    )


def _plan_trace_from_metadata(value: Any) -> PlanTrace | None:
    if value is None:
        return None
    return PlanTrace.model_validate(value)


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

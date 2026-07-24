from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.schemas import AuditEventRecord
from app.storage.database import (
    Database,
    DatabaseRow,
    configured_database_target,
)


class AuditStore:
    def __init__(
        self,
        target: str | Path,
        *,
        auto_create_schema: bool = True,
    ) -> None:
        self.database = Database(target)
        if auto_create_schema:
            self.database.create_schema()

    @classmethod
    def from_settings(cls) -> "AuditStore":
        return cls(
            configured_database_target(settings.incident_db_path),
            auto_create_schema=settings.database_auto_create_schema,
        )

    def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        actor: str,
        source: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        trace_id: str,
        request_method: str | None = None,
        request_path: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        client_ip: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEventRecord:
        event = AuditEventRecord(
            audit_id=f"audit_{uuid4().hex}",
            tenant_id=tenant_id,
            event_type=event_type,
            actor=actor,
            source=source,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            trace_id=trace_id,
            request_method=request_method,
            request_path=request_path,
            status_code=status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
            metadata=metadata or {},
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    audit_id, tenant_id, event_type, actor, source, action,
                    resource_type, resource_id, outcome, trace_id,
                    request_method, request_path, status_code, duration_ms,
                    client_ip, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.audit_id,
                    event.tenant_id,
                    event.event_type,
                    event.actor,
                    event.source,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.outcome,
                    event.trace_id,
                    event.request_method,
                    event.request_path,
                    event.status_code,
                    event.duration_ms,
                    event.client_ip,
                    json.dumps(
                        event.metadata,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    event.created_at.isoformat(),
                ),
            )
        return event

    def list(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        outcome: str | None = None,
    ) -> list[AuditEventRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            values.append(event_type)
        if outcome:
            clauses.append("outcome = ?")
            values.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM audit_events
                {where}
                ORDER BY created_at DESC, audit_id DESC
                LIMIT ?
                """,
                tuple(values),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def delete_expired(self, retention_days: int | None = None) -> int:
        days = max(
            1,
            retention_days or settings.audit_retention_days,
        )
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self.database.connect() as connection:
            return connection.execute(
                "DELETE FROM audit_events WHERE created_at < ?",
                (cutoff,),
            ).rowcount


def _from_row(row: DatabaseRow) -> AuditEventRecord:
    return AuditEventRecord(
        audit_id=row["audit_id"],
        tenant_id=row["tenant_id"],
        event_type=row["event_type"],
        actor=row["actor"],
        source=row["source"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        outcome=row["outcome"],
        trace_id=row["trace_id"],
        request_method=row["request_method"],
        request_path=row["request_path"],
        status_code=row["status_code"],
        duration_ms=row["duration_ms"],
        client_ip=row["client_ip"],
        metadata=json.loads(row["metadata_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )

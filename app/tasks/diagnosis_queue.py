from __future__ import annotations

from typing import Any

from app.agents import OpsAgent
from app.schemas import (
    AlertSeverity,
    ChatResponse,
    DiagnosisTaskEventRecord,
    DiagnosisTaskEventType,
    DiagnosisTaskRecord,
)
from app.storage import SQLiteIncidentStore, SQLiteTaskStore


class DiagnosisTaskQueue:
    """Local async diagnosis queue.

    This is intentionally small and replaceable. The API depends on the queue
    interface, while the implementation can later move to Celery, Redis Queue,
    or a cloud-native worker.
    """

    def __init__(
        self,
        task_store: SQLiteTaskStore | None = None,
        incident_store: SQLiteIncidentStore | None = None,
    ) -> None:
        self.task_store = task_store or SQLiteTaskStore.from_settings()
        self.incident_store = incident_store or SQLiteIncidentStore.from_settings()

    def submit(
        self,
        source: str,
        question: str,
        session_id: str,
        service: str | None = None,
        severity: AlertSeverity = AlertSeverity.warning,
        labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
    ) -> DiagnosisTaskRecord:
        return self.task_store.create_task(
            source=source,
            question=question,
            session_id=session_id,
            service=service,
            severity=severity,
            labels=labels,
            trigger_metadata=trigger_metadata,
        )

    async def run(self, task_id: str) -> None:
        task = self.task_store.mark_running(task_id)
        try:
            response = await OpsAgent.create_default(incident_store=self.incident_store).analyze(
                question=task.question,
                session_id=task.session_id,
                service=task.service,
                severity=task.severity,
                labels=task.labels,
                trigger_metadata=task.trigger_metadata,
            )
        except Exception as exc:
            self.task_store.mark_failed(task_id, str(exc))
            return

        self._record_response_events(task_id, response)
        self.task_store.mark_succeeded(task_id, response)

    def get(self, task_id: str) -> DiagnosisTaskRecord | None:
        return self.task_store.get_task(task_id)

    def list(self, limit: int = 20) -> list[DiagnosisTaskRecord]:
        return self.task_store.list_tasks(limit=limit)

    def events(self, task_id: str) -> list[DiagnosisTaskEventRecord]:
        return self.task_store.list_events(task_id)

    def _record_response_events(self, task_id: str, response: ChatResponse) -> None:
        for result in response.metadata.get("tool_results", []):
            if not isinstance(result, dict):
                continue

            tool_name = str(result.get("tool_name") or "unknown")
            success = bool(result.get("success"))
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            detail = data.get("summary") or result.get("error") or ""
            self.task_store.append_event(
                task_id=task_id,
                event_type=DiagnosisTaskEventType.tool_result,
                message=f"Tool {tool_name} {'succeeded' if success else 'failed'}.",
                data={
                    "tool_name": tool_name,
                    "success": success,
                    "provider": data.get("provider"),
                    "summary": detail,
                    "error": result.get("error"),
                    "elapsed_ms": result.get("elapsed_ms"),
                },
            )

        retrieved_count = int(response.metadata.get("runbook_retrieved_count") or 0)
        self.task_store.append_event(
            task_id=task_id,
            event_type=DiagnosisTaskEventType.retrieved_docs,
            message=f"Retrieved {retrieved_count} runbook documents.",
            data={
                "retrieved_count": retrieved_count,
                "source_doc_ids": [source.doc_id for source in response.sources],
            },
        )

        incident_id = response.metadata.get("incident_id")
        diagnosis_id = response.metadata.get("diagnosis_id")
        if incident_id:
            self.task_store.append_event(
                task_id=task_id,
                event_type=DiagnosisTaskEventType.incident_persisted,
                message="Incident and diagnosis records persisted.",
                data={
                    "incident_id": incident_id,
                    "diagnosis_id": diagnosis_id,
                },
            )

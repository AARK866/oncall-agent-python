from typing import Any

from app.agents import OpsAgent
from app.schemas import AlertSeverity, DiagnosisTaskRecord
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

        self.task_store.mark_succeeded(task_id, response)

    def get(self, task_id: str) -> DiagnosisTaskRecord | None:
        return self.task_store.get_task(task_id)

    def list(self, limit: int = 20) -> list[DiagnosisTaskRecord]:
        return self.task_store.list_tasks(limit=limit)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents import OpsAgent
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
    OpsGraphCheckpointRecord,
)
from app.storage import SQLiteIncidentStore, SQLiteTaskStore


@dataclass(frozen=True)
class DiagnosisTaskSubmission:
    task: DiagnosisTaskRecord
    alert_group: AlertGroupRecord | None = None
    scheduled: bool = True
    deduplicated: bool = False


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
        alert_group_id: str | None = None,
        service: str | None = None,
        severity: AlertSeverity = AlertSeverity.warning,
        labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
    ) -> DiagnosisTaskRecord:
        return self.task_store.create_task(
            source=source,
            question=question,
            session_id=session_id,
            alert_group_id=alert_group_id,
            service=service,
            severity=severity,
            labels=labels,
            trigger_metadata=trigger_metadata,
        )

    def submit_alert(
        self,
        dedupe_key: str,
        source: str,
        title: str,
        question: str,
        session_id: str,
        service: str | None = None,
        severity: AlertSeverity = AlertSeverity.warning,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
    ) -> DiagnosisTaskSubmission:
        existing_group = self.task_store.get_alert_group_by_dedupe_key(dedupe_key)
        group = self.task_store.upsert_alert_group(
            dedupe_key=dedupe_key,
            source=source,
            title=title,
            service=service,
            severity=severity,
            labels=labels,
            annotations=annotations,
        )

        if existing_group and existing_group.status == AlertGroupStatus.active:
            latest_task = (
                self.task_store.get_task(existing_group.latest_task_id)
                if existing_group.latest_task_id
                else None
            )
            if latest_task and latest_task.status != DiagnosisTaskStatus.failed:
                return DiagnosisTaskSubmission(
                    task=latest_task,
                    alert_group=group,
                    scheduled=False,
                    deduplicated=True,
                )

        metadata = dict(trigger_metadata or {})
        metadata["alert_group_id"] = group.group_id
        metadata["dedupe_key"] = dedupe_key
        task = self.submit(
            source=source,
            question=question,
            session_id=session_id,
            alert_group_id=group.group_id,
            service=service,
            severity=severity,
            labels=labels,
            trigger_metadata=metadata,
        )
        group = self.task_store.attach_task_to_alert_group(group.group_id, task.task_id)
        return DiagnosisTaskSubmission(
            task=task,
            alert_group=group,
            scheduled=True,
            deduplicated=False,
        )

    def resolve_alert(self, dedupe_key: str) -> AlertGroupRecord | None:
        return self.task_store.resolve_alert_group(dedupe_key)

    async def run(self, task_id: str) -> None:
        task = self.task_store.mark_running(task_id)
        trigger_metadata = dict(task.trigger_metadata)
        trigger_metadata["task_id"] = task.task_id
        trigger_metadata["task_source"] = task.source
        try:
            response = await OpsAgent.create_default(incident_store=self.incident_store).analyze(
                question=task.question,
                session_id=task.session_id,
                service=task.service,
                severity=task.severity,
                labels=task.labels,
                trigger_metadata=trigger_metadata,
            )
        except Exception as exc:
            self.task_store.mark_failed(task_id, str(exc))
            return

        self._record_response_events(task_id, response)
        self.task_store.mark_succeeded(task_id, response)
        if task.alert_group_id:
            self.task_store.mark_alert_group_diagnosed(task.alert_group_id, response)

    def get(self, task_id: str) -> DiagnosisTaskRecord | None:
        return self.task_store.get_task(task_id)

    def list(self, limit: int = 20) -> list[DiagnosisTaskRecord]:
        return self.task_store.list_tasks(limit=limit)

    def events(self, task_id: str) -> list[DiagnosisTaskEventRecord]:
        return self.task_store.list_events(task_id)

    def checkpoints(self, task_id: str) -> list[OpsGraphCheckpointRecord]:
        return self.task_store.list_graph_checkpoints(task_id)

    def human_reviews(self, task_id: str) -> list[HumanReviewRequestRecord]:
        return self.task_store.list_human_review_requests_for_task(task_id)

    def get_alert_group(self, group_id: str) -> AlertGroupRecord | None:
        return self.task_store.get_alert_group(group_id)

    def list_alert_groups(self, limit: int = 20) -> list[AlertGroupRecord]:
        return self.task_store.list_alert_groups(limit=limit)

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
                    "retry": data.get("_retry"),
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

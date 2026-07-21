from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents import GraphExecutionCancelled, OpsAgent
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
        rerun_of_task_id: str | None = None,
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
            rerun_of_task_id=rerun_of_task_id,
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

    def rerun(
        self,
        task_id: str,
        requested_by: str = "manual",
        reason: str | None = None,
        force: bool = False,
    ) -> DiagnosisTaskRecord:
        original = self.task_store.get_task(task_id)
        if original is None:
            raise KeyError(f"Diagnosis task not found: {task_id}")

        if (
            original.status
            in {
                DiagnosisTaskStatus.queued,
                DiagnosisTaskStatus.running,
                DiagnosisTaskStatus.cancel_requested,
            }
            and not force
        ):
            raise ValueError("Only terminal tasks can be rerun unless force is true.")

        existing_rerun = original.trigger_metadata.get("rerun")
        root_task_id = (
            existing_rerun.get("root_task_id")
            if isinstance(existing_rerun, dict) and existing_rerun.get("root_task_id")
            else original.task_id
        )
        trigger_metadata = dict(original.trigger_metadata)
        trigger_metadata["rerun"] = {
            "of_task_id": original.task_id,
            "root_task_id": root_task_id,
            "requested_by": requested_by,
            "reason": reason,
            "force": force,
            "original_status": original.status.value,
            "original_error": original.error,
        }

        new_task = self.submit(
            source=original.source,
            question=original.question,
            session_id=original.session_id,
            alert_group_id=original.alert_group_id,
            rerun_of_task_id=original.task_id,
            service=original.service,
            severity=original.severity,
            labels=dict(original.labels),
            trigger_metadata=trigger_metadata,
        )
        if original.alert_group_id:
            self.task_store.attach_task_to_alert_group(original.alert_group_id, new_task.task_id)

        self.task_store.append_event(
            task_id=original.task_id,
            event_type=DiagnosisTaskEventType.rerun_requested,
            message="Diagnosis task rerun requested.",
            data={
                "new_task_id": new_task.task_id,
                "requested_by": requested_by,
                "reason": reason,
                "force": force,
            },
        )
        return new_task

    def cancel(
        self,
        task_id: str,
        requested_by: str = "manual",
        reason: str | None = None,
    ) -> DiagnosisTaskRecord:
        task = self.task_store.get_task(task_id)
        if task is None:
            raise KeyError(f"Diagnosis task not found: {task_id}")

        if task.status == DiagnosisTaskStatus.canceled:
            return task
        if task.status == DiagnosisTaskStatus.cancel_requested:
            return task
        if task.status in {DiagnosisTaskStatus.succeeded, DiagnosisTaskStatus.failed}:
            raise ValueError("Only queued or running tasks can be canceled.")

        if task.status == DiagnosisTaskStatus.queued:
            self.task_store.mark_cancel_requested(
                task_id=task.task_id,
                requested_by=requested_by,
                reason=reason,
            )
            return self.task_store.mark_canceled(
                task_id=task.task_id,
                requested_by=requested_by,
                reason=reason,
            )

        return self.task_store.mark_cancel_requested(
            task_id=task.task_id,
            requested_by=requested_by,
            reason=reason,
        )

    def resolve_alert(self, dedupe_key: str) -> AlertGroupRecord | None:
        return self.task_store.resolve_alert_group(dedupe_key)

    async def run(self, task_id: str) -> None:
        queued_task = self.task_store.require_task(task_id)
        if queued_task.status in {
            DiagnosisTaskStatus.cancel_requested,
            DiagnosisTaskStatus.canceled,
        }:
            if queued_task.status == DiagnosisTaskStatus.cancel_requested:
                self.task_store.mark_canceled(task_id, reason="Canceled before execution.")
            return
        if queued_task.status != DiagnosisTaskStatus.queued:
            return

        task = self.task_store.mark_running(task_id)
        trigger_metadata = dict(task.trigger_metadata)
        trigger_metadata["task_id"] = task.task_id
        trigger_metadata["task_source"] = task.source
        try:
            response = await OpsAgent.create_default(
                incident_store=self.incident_store,
                should_cancel=self.task_store.is_cancel_requested,
            ).analyze(
                question=task.question,
                session_id=task.session_id,
                service=task.service,
                severity=task.severity,
                labels=task.labels,
                trigger_metadata=trigger_metadata,
            )
        except GraphExecutionCancelled as exc:
            self.task_store.mark_canceled(task_id, reason=str(exc))
            return
        except Exception as exc:
            self.task_store.mark_failed(task_id, str(exc))
            return

        if self.task_store.is_cancel_requested(task_id):
            self.task_store.mark_canceled(task_id, reason="Canceled before result persistence.")
            return

        self._record_response_events(task_id, response)
        self.task_store.mark_succeeded(task_id, response)
        if task.alert_group_id:
            self.task_store.mark_alert_group_diagnosed(task.alert_group_id, response)

    def get(self, task_id: str) -> DiagnosisTaskRecord | None:
        return self.task_store.get_task(task_id)

    def list(self, limit: int = 20) -> list[DiagnosisTaskRecord]:
        return self.task_store.list_tasks(limit=limit)

    def reruns(self, task_id: str) -> list[DiagnosisTaskRecord]:
        return self.task_store.list_task_reruns(task_id)

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

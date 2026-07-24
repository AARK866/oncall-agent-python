from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.config import settings
from app.security_context import current_tenant_id, tenant_scope
from app.tasks.redis_coordination import RedisCoordinator

DIAGNOSIS_TASK_NAME = "oncall.tasks.run_diagnosis"
KNOWLEDGE_TASK_NAME = "oncall.tasks.run_knowledge_ingestion"
HEALTH_TASK_NAME = "oncall.health.ping"
RECOVERY_TASK_NAME = "oncall.tasks.recover_stale"


class TaskDispatchError(RuntimeError):
    def __init__(self, task_kind: str, business_task_id: str) -> None:
        self.task_kind = task_kind
        self.business_task_id = business_task_id
        super().__init__(
            f"Task broker unavailable for {task_kind} task."
        )


@dataclass(frozen=True)
class DispatchReceipt:
    business_task_id: str
    task_kind: str
    mode: str
    scheduled: bool
    duplicate: bool = False
    broker_task_id: str | None = None


class TaskDispatcher:
    """Routes durable task records to a local callback or Celery broker."""

    def __init__(
        self,
        mode: str | None = None,
        coordinator: RedisCoordinator | None = None,
        celery_application: Any | None = None,
    ) -> None:
        self.mode = (mode or settings.task_queue_mode).strip().lower()
        self.coordinator = coordinator
        self.celery_application = celery_application

    def dispatch_diagnosis(
        self,
        task_id: str,
        background_tasks: Any | None = None,
    ) -> DispatchReceipt:
        return self._dispatch(
            task_kind="diagnosis",
            business_task_id=task_id,
            celery_task_name=DIAGNOSIS_TASK_NAME,
            queue_name="diagnosis",
            background_tasks=background_tasks,
        )

    def dispatch_knowledge_ingestion(
        self,
        task_id: str,
        background_tasks: Any | None = None,
    ) -> DispatchReceipt:
        return self._dispatch(
            task_kind="knowledge",
            business_task_id=task_id,
            celery_task_name=KNOWLEDGE_TASK_NAME,
            queue_name="knowledge",
            background_tasks=background_tasks,
        )

    def _dispatch(
        self,
        task_kind: str,
        business_task_id: str,
        celery_task_name: str,
        queue_name: str,
        background_tasks: Any | None,
    ) -> DispatchReceipt:
        tenant_id = current_tenant_id()
        if self.mode == "local":
            if background_tasks is None:
                raise RuntimeError(
                    "Local task dispatch requires FastAPI BackgroundTasks."
                )
            background_tasks.add_task(
                _tenant_local_runner(task_kind, tenant_id),
                business_task_id,
            )
            return DispatchReceipt(
                business_task_id=business_task_id,
                task_kind=task_kind,
                mode="local",
                scheduled=True,
            )

        if self.mode != "celery":
            raise ValueError(f"Unsupported TASK_QUEUE_MODE: {self.mode}")

        coordinator = self.coordinator or RedisCoordinator()
        reservation = None
        try:
            reservation = coordinator.reserve_dispatch(
                task_kind,
                business_task_id,
            )
        except Exception as exc:
            raise TaskDispatchError(task_kind, business_task_id) from exc
        if reservation is None:
            return DispatchReceipt(
                business_task_id=business_task_id,
                task_kind=task_kind,
                mode="celery",
                scheduled=False,
                duplicate=True,
            )

        broker_task_id = (
            f"{task_kind}-{business_task_id}-{uuid4().hex}"
        )
        application = self.celery_application or _celery_application()
        try:
            application.send_task(
                celery_task_name,
                args=[business_task_id, tenant_id],
                task_id=broker_task_id,
                queue=queue_name,
                retry=True,
                retry_policy={
                    "max_retries": settings.task_broker_publish_max_retries,
                    "interval_start": (
                        settings.task_broker_publish_retry_delay_seconds
                    ),
                    "interval_step": (
                        settings.task_broker_publish_retry_delay_seconds
                    ),
                    "interval_max": 1,
                },
            )
        except Exception as exc:
            try:
                coordinator.release_dispatch(reservation)
            except Exception:
                pass
            raise TaskDispatchError(task_kind, business_task_id) from exc

        return DispatchReceipt(
            business_task_id=business_task_id,
            task_kind=task_kind,
            mode="celery",
            scheduled=True,
            broker_task_id=broker_task_id,
        )


def _local_runner(task_kind: str):
    if task_kind == "diagnosis":
        from app.tasks.diagnosis_queue import DiagnosisTaskQueue

        return DiagnosisTaskQueue().run
    if task_kind == "knowledge":
        from app.tasks.knowledge_ingestion_queue import KnowledgeIngestionQueue

        return KnowledgeIngestionQueue().run
    raise ValueError(f"Unsupported local task kind: {task_kind}")


def _tenant_local_runner(task_kind: str, tenant_id: str):
    runner = _local_runner(task_kind)

    async def run(task_id: str):
        with tenant_scope(tenant_id):
            return await runner(task_id)

    return run


def _celery_application():
    from app.tasks.celery_app import celery_app

    return celery_app

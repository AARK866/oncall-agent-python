from __future__ import annotations

import asyncio
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.observability.audit import AuditStore
from app.security_context import (
    system_database_scope,
    tenant_scope,
)
from app.schemas import (
    DiagnosisTaskStatus,
    KnowledgeIngestionTaskStatus,
)
from app.tasks.celery_app import celery_app
from app.tasks.diagnosis_queue import DiagnosisTaskQueue
from app.tasks.dispatcher import (
    AUDIT_CLEANUP_TASK_NAME,
    DIAGNOSIS_TASK_NAME,
    HEALTH_TASK_NAME,
    KNOWLEDGE_TASK_NAME,
    RECOVERY_TASK_NAME,
    TaskDispatcher,
)
from app.tasks.knowledge_ingestion_queue import KnowledgeIngestionQueue
from app.tasks.redis_coordination import RedisCoordinator

_TRANSIENT_ERRORS = (OperationalError, RedisError, ConnectionError, TimeoutError)


@celery_app.task(
    bind=True,
    name=DIAGNOSIS_TASK_NAME,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_diagnosis_task(
    self,
    task_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    with tenant_scope(tenant_id or settings.default_tenant_id):
        coordinator = RedisCoordinator()
        try:
            with coordinator.execution_lease("diagnosis", task_id) as lease:
                if not lease.acquired:
                    return {
                        "task_id": task_id,
                        "status": "duplicate_ignored",
                    }
                queue = DiagnosisTaskQueue()
                asyncio.run(queue.run(task_id))
                record = queue.get(task_id)
                return {
                    "task_id": task_id,
                    "status": record.status.value if record else "not_found",
                }
        except _TRANSIENT_ERRORS as exc:
            raise self.retry(
                exc=exc,
                countdown=min(2 ** (self.request.retries + 1), 30),
            )


@celery_app.task(
    bind=True,
    name=KNOWLEDGE_TASK_NAME,
    max_retries=6,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_knowledge_ingestion_task(
    self,
    task_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    with tenant_scope(tenant_id or settings.default_tenant_id):
        coordinator = RedisCoordinator()
        try:
            with coordinator.execution_lease("knowledge", task_id) as lease:
                if not lease.acquired:
                    return {
                        "task_id": task_id,
                        "status": "duplicate_ignored",
                    }

                queue = KnowledgeIngestionQueue()
                record = asyncio.run(queue.run(task_id))
                if (
                    record.status == KnowledgeIngestionTaskStatus.failed
                    and record.attempt < settings.knowledge_ingestion_max_attempts
                ):
                    queue.retry(task_id)
                    raise self.retry(
                        countdown=min(2 ** record.attempt, 60),
                    )
                return {
                    "task_id": task_id,
                    "status": record.status.value,
                    "attempt": record.attempt,
                }
        except _TRANSIENT_ERRORS as exc:
            raise self.retry(
                exc=exc,
                countdown=min(2 ** (self.request.retries + 1), 30),
            )


@celery_app.task(name=RECOVERY_TASK_NAME)
def recover_stale_tasks() -> dict[str, Any]:
    queue = DiagnosisTaskQueue()
    with system_database_scope():
        recovered = queue.recover_stale_tasks(
            requested_by="celery-beat",
            reason="Distributed worker heartbeat exceeded the task timeout.",
        )
    resumed_task_ids: list[str] = []
    if (
        settings.stale_task_auto_resume_enabled
        and settings.task_queue_mode.strip().lower() == "celery"
    ):
        dispatcher = TaskDispatcher()
        for task in recovered:
            if task.status != DiagnosisTaskStatus.timed_out:
                continue
            with tenant_scope(
                getattr(task, "tenant_id", settings.default_tenant_id)
            ):
                resumed = queue.resume(
                    task.task_id,
                    requested_by="celery-beat",
                    reason="Automatically resume stale task from latest checkpoint.",
                )
                dispatcher.dispatch_diagnosis(resumed.task_id)
                resumed_task_ids.append(resumed.task_id)

    return {
        "recovered": len(recovered),
        "task_ids": [task.task_id for task in recovered],
        "resumed_task_ids": resumed_task_ids,
    }


@celery_app.task(name=HEALTH_TASK_NAME)
def health_ping(probe: str = "ping") -> dict[str, str]:
    return {"status": "ok", "probe": probe}


@celery_app.task(name=AUDIT_CLEANUP_TASK_NAME)
def cleanup_audit_events() -> dict[str, int]:
    with system_database_scope():
        deleted = AuditStore.from_settings().delete_expired()
    return {
        "deleted": deleted,
        "retention_days": settings.audit_retention_days,
    }

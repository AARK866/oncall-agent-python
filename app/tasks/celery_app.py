from celery import Celery

from app.config import settings
from app.tasks.dispatcher import (
    AUDIT_CLEANUP_TASK_NAME,
    DIAGNOSIS_TASK_NAME,
    HEALTH_TASK_NAME,
    KNOWLEDGE_TASK_NAME,
    RECOVERY_TASK_NAME,
)


celery_app = Celery(
    "oncall_agent",
    broker=settings.redis_url,
    backend=settings.celery_result_backend or settings.redis_url,
    include=["app.tasks.worker_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=settings.celery_result_expires_seconds,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_eager_propagates,
    task_store_eager_result=True,
    broker_transport_options={
        "visibility_timeout": settings.task_execution_lock_ttl_seconds,
    },
    task_default_queue="maintenance",
    task_routes={
        DIAGNOSIS_TASK_NAME: {"queue": "diagnosis"},
        KNOWLEDGE_TASK_NAME: {"queue": "knowledge"},
        HEALTH_TASK_NAME: {"queue": "maintenance"},
        RECOVERY_TASK_NAME: {"queue": "maintenance"},
        AUDIT_CLEANUP_TASK_NAME: {"queue": "maintenance"},
    },
    beat_schedule={
        "recover-stale-diagnosis-tasks": {
            "task": RECOVERY_TASK_NAME,
            "schedule": settings.stale_task_recovery_interval_seconds,
            "options": {"queue": "maintenance"},
        },
        "cleanup-expired-audit-events": {
            "task": AUDIT_CLEANUP_TASK_NAME,
            "schedule": settings.audit_cleanup_interval_seconds,
            "options": {"queue": "maintenance"},
        }
    },
)

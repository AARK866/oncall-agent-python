from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.storage.database import database_from_settings
from app.tasks.redis_coordination import RedisCoordinator

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "version": settings.app_version,
    }


@router.get("/health/database")
def database_health_check():
    database = database_from_settings()
    try:
        database.ping()
    except SQLAlchemyError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "dialect": database.dialect,
                "error_type": type(exc).__name__,
            },
        )

    return {
        "status": "ok",
        "dialect": database.dialect,
        "schema_management": (
            "auto_create"
            if settings.database_auto_create_schema
            else "alembic"
        ),
    }


@router.get("/health/queue")
def queue_health_check():
    mode = settings.task_queue_mode.strip().lower()
    if mode == "local":
        return {
            "status": "ok",
            "mode": "local",
            "broker": "disabled",
        }

    if mode != "celery":
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "mode": mode,
                "error_type": "UnsupportedTaskQueueMode",
            },
        )

    coordinator = RedisCoordinator()
    try:
        coordinator.ping()
        queue_depths = coordinator.queue_depths()
    except RedisError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "mode": "celery",
                "error_type": type(exc).__name__,
            },
        )

    return {
        "status": "ok",
        "mode": "celery",
        "broker": "redis",
        "queue_depths": queue_depths,
    }

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.storage.database import database_from_settings

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

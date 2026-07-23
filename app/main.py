from fastapi import FastAPI

from app.api.alerts import router as alerts_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.knowledge import router as knowledge_router
from app.api.reviews import router as reviews_router
from app.api.tasks import router as tasks_router
from app.api.tools import router as tools_router
from app.api.workflows import router as workflows_router
from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="A learning-oriented intelligent OnCall Agent backend.",
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(alerts_router)
    app.include_router(incidents_router)
    app.include_router(knowledge_router)
    app.include_router(tasks_router)
    app.include_router(reviews_router)
    app.include_router(tools_router)
    app.include_router(workflows_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "message": "OnCall Agent Python is running.",
            "health": "/health",
        }

    return app


app = create_app()

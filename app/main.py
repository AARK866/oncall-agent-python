from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="A learning-oriented intelligent OnCall Agent backend.",
    )

    app.include_router(health_router)
    app.include_router(chat_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "message": "OnCall Agent Python is running.",
            "health": "/health",
        }

    return app


app = create_app()

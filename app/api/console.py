from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

router = APIRouter(tags=["console"])


@router.get("/console", include_in_schema=False)
@router.get("/console/", include_in_schema=False)
async def workflow_console() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")

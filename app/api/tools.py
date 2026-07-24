from fastapi import APIRouter, Depends, Query

from app.schemas import OpsToolHealthResponse
from app.security import require_api_token
from app.tools.health import get_ops_tool_health

router = APIRouter(
    prefix="/api/tools",
    tags=["tools"],
    dependencies=[Depends(require_api_token)],
)


@router.get("/health", response_model=OpsToolHealthResponse)
async def get_tools_health(mode: str | None = Query(default=None)) -> OpsToolHealthResponse:
    return get_ops_tool_health(mode=mode)

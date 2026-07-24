import hmac

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.observability.audit import AuditStore
from app.observability.metrics import metrics_payload
from app.schemas import AuditEventRecord
from app.security import require_api_token

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics endpoint is disabled.",
        )
    _require_metrics_token(request)
    payload, content_type = metrics_payload()
    return Response(
        content=payload,
        headers={"Content-Type": content_type},
    )


@router.get(
    "/api/audit-events",
    response_model=list[AuditEventRecord],
    dependencies=[Depends(require_api_token)],
)
async def list_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
) -> list[AuditEventRecord]:
    return await run_in_threadpool(
        AuditStore.from_settings().list,
        limit=limit,
        event_type=event_type,
        outcome=outcome,
    )


def _require_metrics_token(request: Request) -> None:
    expected = settings.metrics_auth_token
    if not expected:
        return
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not hmac.compare_digest(token.strip(), expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing metrics bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

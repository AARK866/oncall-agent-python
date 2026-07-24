from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.security import require_auth_principal
from app.security_context import AuthPrincipal

router = APIRouter(prefix="/api/auth", tags=["auth"])


class CurrentPrincipalResponse(BaseModel):
    subject: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]
    source: str


@router.get("/me", response_model=CurrentPrincipalResponse)
async def current_identity(
    principal: AuthPrincipal = Depends(require_auth_principal),
) -> CurrentPrincipalResponse:
    return CurrentPrincipalResponse(
        subject=principal.subject,
        tenant_id=principal.tenant_id,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        source=principal.source,
    )

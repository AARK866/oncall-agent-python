from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError, PyJWKClient

from app.config import settings
from app.rag.access_control import KnowledgeAccessContext
from app.security_context import AuthPrincipal, principal_scope

API_KEY_HEADER = "x-api-key"
AUTHORIZATION_HEADER = "authorization"
WEBHOOK_SIGNATURE_HEADER = "x-oncall-signature"
_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "sre": frozenset(
        {
            "alerts:read",
            "audit:read",
            "alerts:write",
            "auth:read",
            "chat:execute",
            "incidents:read",
            "incidents:write",
            "knowledge:read",
            "knowledge:write",
            "reviews:decide",
            "reviews:read",
            "tasks:read",
            "tasks:write",
            "tools:read",
            "workflows:execute",
            "workflows:publish",
            "workflows:read",
            "workflows:review",
            "workflows:write",
        }
    ),
    "oncall": frozenset(
        {
            "alerts:read",
            "alerts:write",
            "auth:read",
            "chat:execute",
            "incidents:read",
            "incidents:write",
            "knowledge:read",
            "reviews:decide",
            "reviews:read",
            "tasks:read",
            "tasks:write",
            "tools:read",
            "workflows:execute",
            "workflows:read",
            "workflows:review",
        }
    ),
    "viewer": frozenset(
        {
            "alerts:read",
            "auth:read",
            "chat:execute",
            "incidents:read",
            "knowledge:read",
            "reviews:read",
            "tasks:read",
            "tools:read",
            "workflows:read",
        }
    ),
}


async def authentication_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Any]],
):
    principal: AuthPrincipal | None = None
    authentication_error: HTTPException | None = None
    try:
        principal = authenticate_request(request, optional=True)
    except HTTPException as exc:
        authentication_error = exc

    if principal is None and not _api_auth_required():
        principal = _local_principal()

    request.state.principal = principal
    request.state.authentication_error = authentication_error
    with principal_scope(principal):
        return await call_next(request)


async def require_api_token(request: Request) -> None:
    await require_auth_principal(request)


async def require_api_principal(request: Request) -> KnowledgeAccessContext:
    principal = await require_auth_principal(request)
    return principal.to_knowledge_context()


async def require_auth_principal(request: Request) -> AuthPrincipal:
    authentication_error = getattr(
        request.state,
        "authentication_error",
        None,
    )
    if authentication_error is not None:
        raise authentication_error

    principal = getattr(request.state, "principal", None)
    if principal is None:
        principal = authenticate_request(request, optional=False)
    if principal is None:
        raise _unauthorized("Authentication is required.")

    permission = required_permission(request)
    if permission and not principal.can(permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {permission}",
        )
    return principal


async def require_webhook_auth(request: Request) -> None:
    if settings.webhook_secret:
        await verify_webhook_signature(request)
        return

    await require_api_token(request)


async def verify_webhook_signature(request: Request) -> None:
    secret = settings.webhook_secret
    if not secret:
        return

    provided_signature = request.headers.get(WEBHOOK_SIGNATURE_HEADER)
    if not provided_signature:
        raise _unauthorized("Missing webhook signature.")

    body = await request.body()
    expected_hex = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected_signature = f"sha256={expected_hex}"
    normalized_provided = provided_signature.strip()
    valid = hmac.compare_digest(normalized_provided, expected_signature) or hmac.compare_digest(
        normalized_provided,
        expected_hex,
    )
    if not valid:
        raise _unauthorized("Invalid webhook signature.")


def authenticate_request(
    request: Request,
    *,
    optional: bool,
) -> AuthPrincipal | None:
    if not _api_auth_required():
        return _local_principal()

    mode = settings.auth_mode.strip().lower()
    if mode == "jwt":
        token = _extract_bearer_token(request)
        if not token:
            if optional:
                return None
            raise _unauthorized("Missing bearer access token.")
        return _decode_jwt_principal(token)

    if mode != "api-token":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unsupported AUTH_MODE: {settings.auth_mode}",
        )

    expected_token = settings.api_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is enabled but API_TOKEN is not configured.",
        )

    provided_token = _extract_api_token(request)
    if not provided_token:
        if optional:
            return None
        raise _unauthorized("Missing API token.")
    if not hmac.compare_digest(provided_token, expected_token):
        raise _unauthorized("Invalid API token.")
    return _principal_from_roles(
        subject=settings.api_token_subject,
        tenant_id=settings.default_tenant_id,
        roles=settings.api_token_roles.split(","),
        source="api_token",
    )


def required_permission(request: Request) -> str | None:
    path = request.url.path
    method = request.method.upper()
    if path.startswith("/api/auth"):
        return "auth:read"
    if path.startswith("/api/audit-events"):
        return "audit:read"
    if path.startswith("/api/chat"):
        return "chat:execute"
    if path.startswith("/api/alerts"):
        return "alerts:read" if method == "GET" else "alerts:write"
    if path.startswith("/api/incidents"):
        return "incidents:read" if method == "GET" else "incidents:write"
    if path.startswith("/api/knowledge"):
        if method == "GET" or path.endswith("/search"):
            return "knowledge:read"
        return "knowledge:write"
    if path.startswith("/api/tasks"):
        return "tasks:read" if method == "GET" else "tasks:write"
    if path.startswith("/api/reviews"):
        return "reviews:read" if method == "GET" else "reviews:decide"
    if path.startswith("/api/tools"):
        return "tools:read"
    if path.startswith("/api/workflows"):
        if method == "GET":
            return "workflows:read"
        if path.endswith("/run"):
            return "workflows:execute"
        if "/reviews/" in path:
            return "workflows:review"
        if path.endswith("/publish") or path.endswith("/rollback"):
            return "workflows:publish"
        return "workflows:write"
    return None


def validate_production_security() -> list[str]:
    missing: list[str] = []
    mode = settings.auth_mode.strip().lower()
    if _api_auth_required():
        if mode == "jwt":
            if not settings.jwt_issuer:
                missing.append("JWT_ISSUER")
            if not settings.jwt_audience:
                missing.append("JWT_AUDIENCE")
            if not settings.jwt_jwks_url and not settings.jwt_secret:
                missing.append("JWT_JWKS_URL or JWT_SECRET")
        elif not settings.api_token:
            missing.append("API_TOKEN")

    if not _is_production() or not settings.require_auth_in_production:
        return missing

    if mode not in {"api-token", "jwt"}:
        missing.append("AUTH_MODE=jwt")
    if not settings.webhook_secret:
        missing.append("WEBHOOK_SECRET")
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if settings.database_auto_create_schema:
        missing.append("DATABASE_AUTO_CREATE_SCHEMA=false")
    if settings.task_queue_mode.strip().lower() != "celery":
        missing.append("TASK_QUEUE_MODE=celery")
    if not settings.redis_url:
        missing.append("REDIS_URL")
    if settings.ops_tool_mode.strip().lower() != "real":
        missing.append("OPS_TOOL_MODE=real")
    if not settings.prometheus_base_url:
        missing.append("PROMETHEUS_BASE_URL")
    if not settings.loki_base_url:
        missing.append("LOKI_BASE_URL")
    if not settings.github_repo:
        missing.append("GITHUB_REPO")
    if not settings.audit_enabled:
        missing.append("AUDIT_ENABLED=true")
    if not settings.audit_persist_enabled:
        missing.append("AUDIT_PERSIST_ENABLED=true")
    if settings.metrics_enabled and not settings.metrics_auth_token:
        missing.append("METRICS_AUTH_TOKEN")
    return sorted(set(missing))


def redact_text(text: str) -> str:
    redacted = text
    for secret in _configured_secrets():
        redacted = redacted.replace(secret, "***")
    return redacted


def permissions_for_roles(roles: Iterable[str]) -> frozenset[str]:
    permissions: set[str] = set()
    for role in _normalized_values(roles):
        permissions.update(ROLE_PERMISSIONS.get(role, ()))
    return frozenset(permissions)


def _decode_jwt_principal(token: str) -> AuthPrincipal:
    try:
        key: Any
        algorithms = {
            value.strip()
            for value in settings.jwt_algorithms.split(",")
            if value.strip()
        }
        if settings.jwt_jwks_url:
            key = _jwks_client(settings.jwt_jwks_url).get_signing_key_from_jwt(token).key
        elif settings.jwt_secret:
            key = settings.jwt_secret
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="JWT validation key is not configured.",
            )

        decode_options: dict[str, Any] = {
            "key": key,
            "algorithms": sorted(algorithms),
            "leeway": settings.jwt_clock_skew_seconds,
            "options": {
                "require": ["exp", "sub"],
                "verify_aud": bool(settings.jwt_audience),
                "verify_iss": bool(settings.jwt_issuer),
            },
        }
        if settings.jwt_audience:
            decode_options["audience"] = settings.jwt_audience
        if settings.jwt_issuer:
            decode_options["issuer"] = settings.jwt_issuer
        claims = jwt.decode(token, **decode_options)
    except HTTPException:
        raise
    except InvalidTokenError as exc:
        raise _unauthorized("Invalid or expired bearer access token.") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT identity provider is unavailable.",
        ) from exc

    tenant_id = str(claims.get(settings.jwt_tenant_claim) or "").strip()
    if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise _unauthorized("Bearer token has an invalid or missing tenant claim.")

    roles = _claim_values(claims.get(settings.jwt_roles_claim))
    explicit_permissions = _claim_values(
        claims.get(settings.jwt_permissions_claim)
    )
    return _principal_from_roles(
        subject=str(claims["sub"]),
        tenant_id=tenant_id,
        roles=roles,
        explicit_permissions=explicit_permissions,
        source="jwt",
    )


def _principal_from_roles(
    *,
    subject: str,
    tenant_id: str,
    roles: Iterable[str],
    explicit_permissions: Iterable[str] = (),
    source: str,
) -> AuthPrincipal:
    normalized_roles = frozenset(_normalized_values(roles))
    permissions = set(permissions_for_roles(normalized_roles))
    permissions.update(_normalized_values(explicit_permissions))
    return AuthPrincipal(
        subject=subject,
        tenant_id=tenant_id,
        roles=normalized_roles,
        permissions=frozenset(permissions),
        authenticated=True,
        source=source,
    )


def _local_principal() -> AuthPrincipal:
    return _principal_from_roles(
        subject="local-development",
        tenant_id=settings.default_tenant_id,
        roles=settings.api_token_roles.split(","),
        source="local",
    )


def _api_auth_required() -> bool:
    return (
        settings.api_auth_enabled
        or settings.auth_mode.strip().lower() == "jwt"
        or (settings.require_auth_in_production and _is_production())
    )


def _is_production() -> bool:
    return settings.app_env.strip().lower() in {"prod", "production"}


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get(AUTHORIZATION_HEADER, "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return None


def _extract_api_token(request: Request) -> str | None:
    header_token = request.headers.get(API_KEY_HEADER)
    if header_token:
        return header_token.strip()
    return _extract_bearer_token(request)


def _claim_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,]+", value) if item]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def _normalized_values(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _jwks_client(url: str) -> PyJWKClient:
    if not hasattr(_jwks_client, "_clients"):
        _jwks_client._clients = {}  # type: ignore[attr-defined]
    clients: dict[str, PyJWKClient] = _jwks_client._clients  # type: ignore[attr-defined]
    if url not in clients:
        clients[url] = PyJWKClient(url, timeout=settings.llm_timeout_seconds)
    return clients[url]


def _configured_secrets() -> list[str]:
    return [
        secret
        for secret in [
            settings.api_token,
            settings.jwt_secret,
            settings.metrics_auth_token,
            settings.webhook_secret,
            settings.database_url,
            settings.redis_url,
            settings.prometheus_bearer_token,
            settings.prometheus_password,
            settings.loki_bearer_token,
            settings.loki_password,
            settings.llm_api_key,
            settings.embedding_api_key,
            settings.github_token,
            settings.github_proxy_url,
            settings.gitlab_token,
            settings.milvus_token,
        ]
        if secret
    ]

from __future__ import annotations

import logging
import re
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.observability.audit import AuditStore
from app.observability.context import trace_scope
from app.observability.metrics import (
    AUDIT_WRITE_FAILURES,
    HTTP_IN_PROGRESS,
    observe_http_request,
)
from app.security_context import AuthPrincipal, principal_scope

logger = logging.getLogger(__name__)

TRACE_HEADER = "x-trace-id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_TRACEPARENT_PATTERN = re.compile(
    r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)


async def observability_middleware(request: Request, call_next):
    trace_id = _request_trace_id(request)
    request.state.trace_id = trace_id
    started_at = perf_counter()
    method = request.method.upper()
    status_code = 500
    HTTP_IN_PROGRESS.labels(method=method).inc()

    with trace_scope(trace_id):
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Trace-ID"] = trace_id
            return response
        except Exception:
            logger.exception(
                "Unhandled HTTP request failure.",
                extra={
                    "event": "http.request",
                    "outcome": "failure",
                    "method": method,
                    "route": request.url.path,
                    "status_code": 500,
                },
            )
            raise
        finally:
            duration_seconds = perf_counter() - started_at
            route = _route_template(request)
            HTTP_IN_PROGRESS.labels(method=method).dec()
            observe_http_request(
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            logger.info(
                "HTTP request completed.",
                extra={
                    "event": "http.request",
                    "outcome": _outcome(status_code),
                    "method": method,
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": int(duration_seconds * 1000),
                },
            )
            if settings.audit_enabled and route.startswith("/api"):
                await _persist_audit(
                    request=request,
                    trace_id=trace_id,
                    route=route,
                    status_code=status_code,
                    duration_ms=int(duration_seconds * 1000),
                )


async def _persist_audit(
    *,
    request: Request,
    trace_id: str,
    route: str,
    status_code: int,
    duration_ms: int,
) -> None:
    if not settings.audit_persist_enabled:
        return
    principal: AuthPrincipal | None = getattr(
        request.state,
        "principal",
        None,
    )
    tenant_id = (
        principal.tenant_id if principal else settings.default_tenant_id
    )
    actor = principal.subject if principal else "anonymous"
    try:
        with principal_scope(principal):
            await run_in_threadpool(
                AuditStore.from_settings().append,
                tenant_id=tenant_id,
                event_type="api.request",
                actor=actor,
                source="http",
                action=f"{request.method.upper()} {route}",
                resource_type="api_route",
                resource_id=None,
                outcome=_outcome(status_code),
                trace_id=trace_id,
                request_method=request.method.upper(),
                request_path=route,
                status_code=status_code,
                duration_ms=duration_ms,
                client_ip=(
                    request.client.host if request.client else None
                ),
                metadata={
                    "auth_source": (
                        principal.source if principal else "anonymous"
                    )
                },
            )
    except Exception:
        AUDIT_WRITE_FAILURES.inc()
        logger.exception(
            "Audit event persistence failed.",
            extra={
                "event": "audit.write",
                "outcome": "failure",
                "route": route,
                "status_code": status_code,
            },
        )


def _request_trace_id(request: Request) -> str:
    traceparent = request.headers.get("traceparent", "").strip().lower()
    match = _TRACEPARENT_PATTERN.fullmatch(traceparent)
    if match and match.group(1) != ("0" * 32):
        return match.group(1)

    candidate = request.headers.get(TRACE_HEADER, "").strip()
    if _TRACE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)


def _outcome(status_code: int) -> str:
    if status_code in {401, 403}:
        return "denied"
    if status_code < 400:
        return "success"
    if status_code < 500:
        return "client_error"
    return "failure"

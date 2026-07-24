from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

HTTP_REQUESTS = Counter(
    "oncall_http_requests_total",
    "HTTP requests handled by the OnCall Agent API.",
    ("method", "route", "status_class"),
)
HTTP_REQUEST_DURATION = Histogram(
    "oncall_http_request_duration_seconds",
    "HTTP request latency.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
HTTP_IN_PROGRESS = Gauge(
    "oncall_http_requests_in_progress",
    "HTTP requests currently being processed.",
    ("method",),
)
TOOL_CALLS = Counter(
    "oncall_tool_calls_total",
    "Agent tool calls.",
    ("tool", "connector", "outcome"),
)
TOOL_DURATION = Histogram(
    "oncall_tool_call_duration_seconds",
    "Agent tool execution latency.",
    ("tool", "connector"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
TASK_DISPATCHES = Counter(
    "oncall_task_dispatches_total",
    "Diagnosis and ingestion task dispatch attempts.",
    ("task_kind", "mode", "outcome"),
)
AUDIT_WRITE_FAILURES = Counter(
    "oncall_audit_write_failures_total",
    "Audit records that could not be persisted.",
)


def observe_http_request(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    HTTP_REQUESTS.labels(
        method=method,
        route=route,
        status_class=f"{status_code // 100}xx",
    ).inc()
    HTTP_REQUEST_DURATION.labels(
        method=method,
        route=route,
    ).observe(duration_seconds)


def observe_tool_call(
    *,
    tool_name: str,
    connector: str,
    success: bool,
    duration_seconds: float,
) -> None:
    TOOL_CALLS.labels(
        tool=tool_name,
        connector=connector,
        outcome="success" if success else "failure",
    ).inc()
    TOOL_DURATION.labels(
        tool=tool_name,
        connector=connector,
    ).observe(duration_seconds)


def observe_task_dispatch(
    *,
    task_kind: str,
    mode: str,
    outcome: str,
) -> None:
    TASK_DISPATCHES.labels(
        task_kind=task_kind,
        mode=mode,
        outcome=outcome,
    ).inc()


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST

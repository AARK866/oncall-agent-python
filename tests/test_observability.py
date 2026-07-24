import asyncio
import json
import logging

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.observability.audit import AuditStore
from app.observability.logging import JsonLogFormatter
from app.schemas import ToolCall
from app.tools import create_mock_ops_registry


client = TestClient(app)


def test_http_trace_id_is_returned_and_preserved() -> None:
    response = client.get(
        "/health",
        headers={"X-Trace-ID": "trace-test-123456"},
    )
    generated = client.get(
        "/health",
        headers={"X-Trace-ID": "invalid"},
    )

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == "trace-test-123456"
    assert len(generated.headers["X-Trace-ID"]) == 32


def test_metrics_endpoint_supports_bearer_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "metrics_auth_token",
        "metrics-test-token",
    )

    denied = client.get("/metrics")
    allowed = client.get(
        "/metrics",
        headers={
            "Authorization": "Bearer metrics-test-token",
        },
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert "oncall_http_requests_total" in allowed.text
    assert "oncall_http_request_duration_seconds" in allowed.text


def test_tool_execution_is_exported_as_prometheus_metric() -> None:
    result = asyncio.run(
        create_mock_ops_registry().execute(
            ToolCall(
                name="query_metrics",
                arguments={"service": "payment-api"},
            )
        )
    )
    metrics = client.get("/metrics")

    assert result.success is True
    assert (
        'oncall_tool_calls_total{connector="mock_ops",'
        'outcome="success",tool="query_metrics"}'
    ) in metrics.text


def test_audit_store_persists_and_filters_events(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    stored = store.append(
        tenant_id="default",
        event_type="api.request",
        actor="test-user",
        source="http",
        action="POST /api/tasks/{task_id}/cancel",
        resource_type="api_route",
        resource_id="task-1",
        outcome="success",
        trace_id="trace-audit-123",
        request_method="POST",
        request_path="/api/tasks/{task_id}/cancel",
        status_code=200,
        duration_ms=12,
        metadata={"reason": "test"},
    )

    events = store.list(
        event_type="api.request",
        outcome="success",
    )

    assert events == [stored]
    assert events[0].metadata == {"reason": "test"}


def test_api_request_is_written_to_audit_log(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "database_url", None)
    monkeypatch.setattr(
        settings,
        "incident_db_path",
        str(tmp_path / "api-audit.db"),
    )
    monkeypatch.setattr(settings, "audit_persist_enabled", True)

    response = client.get(
        "/api/tools/health?mode=mock",
        headers={"X-Trace-ID": "trace-api-audit-123"},
    )
    audit_response = client.get("/api/audit-events")

    assert response.status_code == 200
    assert audit_response.status_code == 200
    events = audit_response.json()
    matching = [
        event
        for event in events
        if event["trace_id"] == "trace-api-audit-123"
    ]
    assert len(matching) == 1
    assert matching[0]["actor"] == "local-development"
    assert matching[0]["action"] == "GET /api/tools/health"
    assert matching[0]["outcome"] == "success"


def test_json_logs_include_trace_context_and_redact_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "secret-log-value")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=secret-log-value",
        args=(),
        exc_info=None,
    )
    record.event = "test.event"

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["message"] == "token=***"
    assert payload["event"] == "test.event"
    assert "tenant_id" in payload

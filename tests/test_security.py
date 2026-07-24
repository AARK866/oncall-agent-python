import asyncio
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.security import redact_text
from scripts.check_enterprise_stack import _check_config


client = TestClient(app)


def test_protected_task_endpoint_requires_api_token_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "test-api-token")

    response = client.get("/api/tasks")

    assert response.status_code == 401


def test_protected_task_endpoint_accepts_x_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "test-api-token")

    response = client.get("/api/tasks", headers={"X-API-Key": "test-api-token"})

    assert response.status_code == 200


def test_protected_task_endpoint_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "test-api-token")

    response = client.get("/api/tasks", headers={"Authorization": "Bearer test-api-token"})

    assert response.status_code == 200


def test_alertmanager_webhook_requires_valid_signature(monkeypatch) -> None:
    monkeypatch.setattr(settings, "webhook_secret", "webhook-secret")
    payload = _alertmanager_payload()

    missing_response = client.post("/api/alerts/alertmanager", json=payload)
    invalid_response = client.post(
        "/api/alerts/alertmanager",
        json=payload,
        headers={"X-OnCall-Signature": "sha256=invalid"},
    )

    assert missing_response.status_code == 401
    assert invalid_response.status_code == 401


def test_alertmanager_webhook_accepts_valid_signature(monkeypatch) -> None:
    secret = "webhook-secret"
    monkeypatch.setattr(settings, "webhook_secret", secret)
    payload = _alertmanager_payload()
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/alerts/alertmanager",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-OnCall-Signature": f"sha256={signature}",
        },
    )

    assert response.status_code == 202


def test_production_config_reports_missing_security_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(settings, "webhook_secret", None)

    result = asyncio.run(_check_config())

    assert result.status == "FAIL"
    assert "API_TOKEN" in result.detail
    assert "WEBHOOK_SECRET" in result.detail


def test_production_config_rejects_mock_ops_backends(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "ops_tool_mode", "mock")
    monkeypatch.setattr(settings, "prometheus_base_url", None)
    monkeypatch.setattr(settings, "loki_base_url", None)
    monkeypatch.setattr(settings, "github_repo", None)

    result = asyncio.run(_check_config())

    assert result.status == "FAIL"
    assert "OPS_TOOL_MODE=real" in result.detail
    assert "PROMETHEUS_BASE_URL" in result.detail
    assert "LOKI_BASE_URL" in result.detail
    assert "GITHUB_REPO" in result.detail


def test_production_config_requires_protected_metrics_and_persistent_audit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_auth_token", None)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "audit_persist_enabled", False)

    result = asyncio.run(_check_config())

    assert result.status == "FAIL"
    assert "METRICS_AUTH_TOKEN" in result.detail
    assert "AUDIT_ENABLED=true" in result.detail
    assert "AUDIT_PERSIST_ENABLED=true" in result.detail


def test_production_config_requires_distributed_langgraph_state(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "ops_graph_runtime", "local")
    monkeypatch.setattr(settings, "ops_graph_checkpointer", "memory")
    monkeypatch.setattr(settings, "workflow_checkpointer", "sqlite")

    result = asyncio.run(_check_config())

    assert result.status == "FAIL"
    assert "OPS_GRAPH_RUNTIME=langgraph" in result.detail
    assert "OPS_GRAPH_CHECKPOINTER=postgres" in result.detail
    assert "WORKFLOW_CHECKPOINTER=postgres" in result.detail


def test_redact_text_hides_security_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_token", "api-secret")
    monkeypatch.setattr(settings, "webhook_secret", "webhook-secret")

    assert redact_text("api-secret webhook-secret") == "*** ***"


def _alertmanager_payload() -> dict:
    return {
        "status": "firing",
        "receiver": "oncall-agent",
        "commonLabels": {"service": "payment-api", "severity": "critical"},
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "SecurityTestAlert"},
                "startsAt": "2026-07-08T10:00:00Z",
                "fingerprint": "security-test-fingerprint",
            }
        ],
    }

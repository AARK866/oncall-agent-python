import json

import pytest
from fastapi.testclient import TestClient

from scripts.ship_logs_to_loki import build_loki_streams
from services.payment_api.main import (
    PaymentApiSettings,
    create_payment_app,
)


def _settings(tmp_path, **overrides) -> PaymentApiSettings:
    values = {
        "environment": "local",
        "database_path": tmp_path / "payment.db",
        "log_file_path": tmp_path / "payment-api.log",
        "fault_injection_enabled": False,
        "fault_admin_token": None,
    }
    values.update(overrides)
    return PaymentApiSettings(**values)


def _payment(order_id: str = "order-1001") -> dict:
    return {
        "order_id": order_id,
        "user_id": "user-1001",
        "amount": 9900,
        "currency": "CNY",
        "channel": "card",
    }


def test_payment_health_idempotency_refund_and_metrics(
    tmp_path,
) -> None:
    client = TestClient(
        create_payment_app(_settings(tmp_path))
    )

    health = client.get("/health")
    ready = client.get("/ready")
    first = client.post("/pay", json=_payment())
    replay = client.post("/pay", json=_payment())
    payment_id = first.json()["payment_id"]
    fetched = client.get(f"/payments/{payment_id}")
    refunded = client.post(
        "/refund",
        json={
            "payment_id": payment_id,
            "reason": "customer_request",
        },
    )
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["service"] == "payment-api"
    assert health.json()["fault_injection_enabled"] is False
    assert ready.status_code == 200
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["payment_id"] == payment_id
    assert fetched.json()["status"] == "paid"
    assert refunded.json()["status"] == "refunded"
    assert 'service="payment-api"' in metrics.text
    assert 'status="200"' in metrics.text
    assert "payment_requests_total" in metrics.text


def test_fault_injection_is_disabled_by_default(tmp_path) -> None:
    client = TestClient(
        create_payment_app(_settings(tmp_path))
    )

    response = client.post(
        "/admin/fault/5xx",
        headers={"X-Admin-Token": "not-configured"},
        json={"enabled": True, "ratio": 1},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "fault injection is disabled"


def test_controlled_5xx_is_secured_observable_and_resettable(
    tmp_path,
) -> None:
    token = "payment-local-token-123"
    settings = _settings(
        tmp_path,
        fault_injection_enabled=True,
        fault_admin_token=token,
    )
    client = TestClient(create_payment_app(settings))

    denied = client.post(
        "/admin/fault/5xx",
        headers={"X-Admin-Token": "wrong-token"},
        json={"enabled": True, "ratio": 1},
    )
    enabled = client.post(
        "/admin/fault/5xx",
        headers={"X-Admin-Token": token},
        json={"enabled": True, "ratio": 1},
    )
    failed = client.post("/pay", json=_payment())
    metrics = client.get("/metrics")
    reset = client.post(
        "/admin/fault/reset",
        headers={"X-Admin-Token": token},
    )
    recovered = client.post(
        "/pay",
        json=_payment("order-1002"),
    )

    assert denied.status_code == 403
    assert enabled.status_code == 200
    assert failed.status_code == 500
    assert 'status="500"' in metrics.text
    assert 'reason="injected_5xx"' in metrics.text
    assert reset.status_code == 200
    assert recovered.status_code == 200

    lines = settings.log_file_path.read_text(
        encoding="utf-8"
    ).splitlines()
    payloads = [json.loads(line) for line in lines]
    streams = build_loki_streams(
        lines,
        default_service="payment-api",
    )

    assert any(
        payload["level"] == "ERROR"
        and payload["service"] == "payment-api"
        for payload in payloads
    )
    assert any(
        stream["stream"]["service"] == "payment-api"
        and stream["stream"]["level"] == "ERROR"
        for stream in streams
    )


def test_fault_settings_fail_closed() -> None:
    with pytest.raises(ValueError, match="16 characters"):
        PaymentApiSettings(
            fault_injection_enabled=True,
            fault_admin_token="short",
        )

    with pytest.raises(ValueError, match="production"):
        PaymentApiSettings(
            environment="production",
            fault_injection_enabled=True,
            fault_admin_token="payment-production-token",
        )

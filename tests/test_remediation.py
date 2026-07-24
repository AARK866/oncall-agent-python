import asyncio
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.remediation import PaymentApiRemediationController
from app.schemas import ToolResult


client = TestClient(app)


def _plan(controller: PaymentApiRemediationController):
    plan = controller.plan(
        service="payment-api",
        labels={
            "alertname": "PaymentApiHigh5xxRatio",
            "incident_type": "5xx",
        },
        trigger_metadata={"source": "alertmanager"},
    )
    assert plan is not None
    return plan


def test_remediation_policy_allows_only_known_alertmanager_actions() -> None:
    enabled = PaymentApiRemediationController(
        enabled=True,
        base_url="http://payment-api:8010",
        admin_token="payment-admin-token",
    )
    disabled = PaymentApiRemediationController(enabled=False)

    assert _plan(enabled).action == "reset_payment_faults"
    assert (
        enabled.plan(
            service="payment-api",
            labels={"alertname": "PaymentApiDown"},
            trigger_metadata={"source": "alertmanager"},
        )
        is None
    )
    assert (
        enabled.plan(
            service="order-api",
            labels={
                "alertname": "PaymentApiHigh5xxRatio"
            },
            trigger_metadata={"source": "alertmanager"},
        )
        is None
    )
    assert (
        enabled.plan(
            service="payment-api",
            labels={
                "alertname": "PaymentApiHigh5xxRatio"
            },
            trigger_metadata={"source": "api_alert"},
        )
        is None
    )
    assert (
        disabled.plan(
            service="payment-api",
            labels={
                "alertname": "PaymentApiHigh5xxRatio"
            },
            trigger_metadata={"source": "alertmanager"},
        )
        is None
    )


def test_remediation_executor_uses_fixed_endpoint_and_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/admin/fault/reset"
        assert (
            request.headers["X-Admin-Token"]
            == "payment-admin-token"
        )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "fault_state": {
                    "error_5xx_enabled": False,
                    "latency_enabled": False,
                    "channel_failure_enabled": False,
                },
            },
        )

    controller = PaymentApiRemediationController(
        enabled=True,
        base_url="http://payment-api:8010",
        admin_token="payment-admin-token",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(controller.execute(_plan(controller)))

    assert result.success is True
    assert result.tool_name == "reset_payment_faults"
    assert result.data["provider"] == "payment-api"
    assert result.data["endpoint"] == "/admin/fault/reset"
    assert result.data["_retry"]["attempts"] == 1


def test_remediation_executor_fails_closed_on_unsafe_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "fault_state": {
                    "error_5xx_enabled": True,
                    "latency_enabled": False,
                    "channel_failure_enabled": False,
                },
            },
        )

    controller = PaymentApiRemediationController(
        enabled=True,
        base_url="http://payment-api:8010",
        admin_token="payment-admin-token",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(controller.execute(_plan(controller)))

    assert result.success is False
    assert "still reports an enabled fault" in result.error


def test_approved_alert_executes_allowlisted_remediation_once(
    monkeypatch,
) -> None:
    executed = []

    async def fake_execute(self, plan) -> ToolResult:
        executed.append(plan)
        return ToolResult(
            tool_name="reset_payment_faults",
            success=True,
            data={
                "provider": "payment-api",
                "action": "reset_payment_faults",
                "fault_state": {
                    "error_5xx_enabled": False,
                    "latency_enabled": False,
                    "channel_failure_enabled": False,
                },
                "summary": "Approved reset completed.",
            },
            elapsed_ms=5,
        )

    monkeypatch.setattr(
        settings,
        "payment_api_remediation_enabled",
        True,
    )
    monkeypatch.setattr(
        settings,
        "payment_api_base_url",
        "http://payment-api:8010",
    )
    monkeypatch.setattr(
        settings,
        "payment_api_fault_admin_token",
        "payment-admin-token",
    )
    monkeypatch.setattr(
        PaymentApiRemediationController,
        "execute",
        fake_execute,
    )

    response = client.post(
        "/api/alerts/alertmanager",
        json={
            "status": "firing",
            "receiver": "oncall-agent",
            "commonLabels": {
                "service": "payment-api",
                "severity": "critical",
            },
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": (
                            "PaymentApiHigh5xxRatio"
                        ),
                        "incident_type": "5xx",
                    },
                    "annotations": {
                        "summary": (
                            "payment-api 5xx ratio is high"
                        )
                    },
                    "startsAt": "2026-07-24T10:00:00Z",
                    "fingerprint": (
                        f"remediation-{uuid4().hex}"
                    ),
                }
            ],
        },
    )
    assert response.status_code == 202
    task_id = response.json()["tasks"][0]["task_id"]
    waiting = client.get(f"/api/tasks/{task_id}").json()
    reviews = client.get(
        f"/api/tasks/{task_id}/reviews"
    ).json()

    assert waiting["status"] == "waiting_review"
    assert executed == []
    assert (
        waiting["result"]["metadata"]["remediation"]["planned"]
        is True
    )
    assert (
        reviews[0]["metadata"]["remediation_plan"]["action"]
        == "reset_payment_faults"
    )

    approved = client.post(
        f"/api/reviews/{reviews[0]['review_id']}/approve",
        json={
            "reviewer": "sre-alice",
            "reason": "Evidence confirmed the controlled drill.",
        },
    )
    completed = client.get(f"/api/tasks/{task_id}").json()
    event_types = [
        event["event_type"]
        for event in client.get(
            f"/api/tasks/{task_id}/events"
        ).json()
    ]

    assert approved.status_code == 200
    assert len(executed) == 1
    assert completed["status"] == "succeeded"
    remediation = completed["result"]["metadata"][
        "remediation"
    ]
    assert remediation["status"] == "succeeded"
    assert (
        remediation["result"]["data"]["approval"][0][
            "reviewer"
        ]
        == "sre-alice"
    )
    assert "remediation_started" in event_types
    assert "remediation_succeeded" in event_types

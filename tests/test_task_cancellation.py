import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AlertSeverity
from app.tasks import DiagnosisTaskQueue


client = TestClient(app)


def test_queued_task_can_be_canceled_from_api() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="payment-api latency is high",
        session_id=f"cancel-queued-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.warning,
    )

    response = client.post(
        f"/api/tasks/{task.task_id}/cancel",
        json={
            "requested_by": "alice",
            "reason": "Duplicate investigation.",
        },
    )

    assert response.status_code == 202
    canceled = response.json()
    assert canceled["status"] == "canceled"
    assert canceled["finished_at"] is not None

    event_types = _get_task_event_types(task.task_id)
    assert event_types == ["queued", "cancel_requested", "canceled"]

    asyncio.run(queue.run(task.task_id))
    assert _get_task(task.task_id)["status"] == "canceled"


def test_running_task_records_cancel_request_then_cancels_before_next_run() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="payment-api 5xx is high",
        session_id=f"cancel-running-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.critical,
    )
    queue.task_store.mark_running(task.task_id)

    response = client.post(
        f"/api/tasks/{task.task_id}/cancel",
        json={
            "requested_by": "alice",
            "reason": "Operator took manual ownership.",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "cancel_requested"

    asyncio.run(queue.run(task.task_id))
    canceled = _get_task(task.task_id)
    assert canceled["status"] == "canceled"

    event_types = _get_task_event_types(task.task_id)
    assert "cancel_requested" in event_types
    assert event_types[-1] == "canceled"


def test_succeeded_task_cannot_be_canceled() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"cancel-completed-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "annotations": {"summary": "payment-api has elevated 5xx after a deployment"},
        },
    )
    assert response.status_code == 202
    task_id = response.json()["tasks"][0]["task_id"]
    assert _get_task(task_id)["status"] == "succeeded"

    cancel_response = client.post(
        f"/api/tasks/{task_id}/cancel",
        json={"requested_by": "alice", "reason": "Too late."},
    )

    assert cancel_response.status_code == 409


def test_missing_task_cancel_returns_404() -> None:
    response = client.post(
        "/api/tasks/task_missing/cancel",
        json={"requested_by": "alice"},
    )

    assert response.status_code == 404


def _get_task(task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    return response.json()


def _get_task_event_types(task_id: str) -> list[str]:
    response = client.get(f"/api/tasks/{task_id}/events")
    assert response.status_code == 200
    return [event["event_type"] for event in response.json()]

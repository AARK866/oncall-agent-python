from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AlertSeverity, DiagnosisTaskEventType
from app.storage import SQLiteTaskStore
from app.tasks import DiagnosisTaskQueue


client = TestClient(app)


def test_stale_running_task_is_marked_timed_out_from_api() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="payment-api 5xx is high",
        session_id=f"timeout-running-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.critical,
    )
    queue.task_store.mark_running(task.task_id)
    _age_task(queue.task_store, task.task_id, seconds=3600)

    response = client.post(
        "/api/tasks/recover-stale",
        json={
            "requested_by": "watchdog",
            "reason": "Worker heartbeat expired.",
            "max_age_seconds": 60,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recovered"] >= 1
    recovered = _task_by_id(data["tasks"], task.task_id)
    assert recovered is not None
    assert recovered["status"] == "timed_out"
    assert recovered["error"] == "Worker heartbeat expired."

    event_types = _get_task_event_types(task.task_id)
    assert DiagnosisTaskEventType.timed_out.value in event_types


def test_stale_cancel_request_is_marked_canceled() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="payment-api latency is high",
        session_id=f"timeout-cancel-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.warning,
    )
    queue.task_store.mark_running(task.task_id)
    queue.task_store.mark_cancel_requested(
        task_id=task.task_id,
        requested_by="alice",
        reason="Stop this investigation.",
    )
    _age_task(queue.task_store, task.task_id, seconds=3600)

    response = client.post(
        "/api/tasks/recover-stale",
        json={
            "requested_by": "watchdog",
            "max_age_seconds": 60,
        },
    )

    assert response.status_code == 200
    recovered = _task_by_id(response.json()["tasks"], task.task_id)
    assert recovered is not None
    assert recovered["status"] == "canceled"

    event_types = _get_task_event_types(task.task_id)
    assert event_types[-1] == DiagnosisTaskEventType.canceled.value


def test_recent_running_task_is_not_recovered() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="order-api latency is high",
        session_id=f"timeout-recent-{uuid4().hex}",
        service="order-api",
        severity=AlertSeverity.warning,
    )
    queue.task_store.mark_running(task.task_id)

    response = client.post(
        "/api/tasks/recover-stale",
        json={
            "requested_by": "watchdog",
            "max_age_seconds": 86400,
        },
    )

    assert response.status_code == 200
    assert _task_by_id(response.json()["tasks"], task.task_id) is None
    assert _get_task(task.task_id)["status"] == "running"
    queue.task_store.mark_canceled(task.task_id, reason="Test cleanup.")


def test_timed_out_task_can_be_rerun() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="payment-api 5xx is high",
        session_id=f"timeout-rerun-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.critical,
    )
    queue.task_store.mark_running(task.task_id)
    _age_task(queue.task_store, task.task_id, seconds=3600)
    client.post(
        "/api/tasks/recover-stale",
        json={
            "requested_by": "watchdog",
            "max_age_seconds": 60,
        },
    )

    response = client.post(
        f"/api/tasks/{task.task_id}/rerun",
        json={"requested_by": "alice", "reason": "Retry timed-out diagnosis."},
    )

    assert response.status_code == 202
    assert response.json()["rerun_of_task_id"] == task.task_id


def _age_task(store: SQLiteTaskStore, task_id: str, seconds: int) -> None:
    stale_at = (datetime.utcnow() - timedelta(seconds=seconds)).isoformat()
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE diagnosis_tasks
            SET started_at = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (stale_at, stale_at, task_id),
        )


def _task_by_id(tasks: list[dict], task_id: str) -> dict | None:
    return next((task for task in tasks if task["task_id"] == task_id), None)


def _get_task(task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    return response.json()


def _get_task_event_types(task_id: str) -> list[str]:
    response = client.get(f"/api/tasks/{task_id}/events")
    assert response.status_code == 200
    return [event["event_type"] for event in response.json()]

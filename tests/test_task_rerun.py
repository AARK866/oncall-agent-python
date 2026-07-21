from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AlertSeverity
from app.tasks import DiagnosisTaskQueue


client = TestClient(app)


def test_succeeded_task_can_be_rerun_from_api() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"rerun-payment-5xx-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "labels": {"team": "payments"},
            "annotations": {"summary": "payment-api has elevated 5xx after a deployment"},
        },
    )
    assert response.status_code == 202
    original_task_id = response.json()["tasks"][0]["task_id"]
    original_task = _get_task(original_task_id)
    assert original_task["status"] == "succeeded"

    rerun_response = client.post(
        f"/api/tasks/{original_task_id}/rerun",
        json={
            "requested_by": "alice",
            "reason": "Run again after fixing the Loki connector.",
        },
    )

    assert rerun_response.status_code == 202
    new_task = rerun_response.json()
    assert new_task["task_id"] != original_task_id
    assert new_task["rerun_of_task_id"] == original_task_id
    assert new_task["thread_id"] == original_task["thread_id"]
    assert new_task["run_id"] != original_task["run_id"]
    assert new_task["source"] == original_task["source"]
    assert new_task["service"] == original_task["service"]
    assert new_task["trigger_metadata"]["rerun"]["requested_by"] == "alice"
    assert new_task["trigger_metadata"]["rerun"]["root_task_id"] == original_task_id

    completed_rerun = _get_task(new_task["task_id"])
    assert completed_rerun["status"] == "succeeded"
    assert completed_rerun["rerun_of_task_id"] == original_task_id
    assert completed_rerun["result"]["metadata"]["trigger"]["rerun"]["reason"] == (
        "Run again after fixing the Loki connector."
    )

    original_events = _get_task_events(original_task_id)
    rerun_events = [event for event in original_events if event["event_type"] == "rerun_requested"]
    assert rerun_events
    assert rerun_events[-1]["data"]["new_task_id"] == new_task["task_id"]

    reruns_response = client.get(f"/api/tasks/{original_task_id}/reruns")
    assert reruns_response.status_code == 200
    assert [task["task_id"] for task in reruns_response.json()] == [new_task["task_id"]]


def test_queued_task_requires_force_to_rerun() -> None:
    queued_task = DiagnosisTaskQueue().submit(
        source="manual",
        question="payment-api latency is high",
        session_id=f"queued-rerun-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.warning,
    )

    response = client.post(
        f"/api/tasks/{queued_task.task_id}/rerun",
        json={"requested_by": "alice", "reason": "Try duplicate run."},
    )

    assert response.status_code == 409
    assert "force" in response.json()["detail"]


def test_missing_task_rerun_returns_404() -> None:
    response = client.post(
        "/api/tasks/task_missing/rerun",
        json={"requested_by": "alice"},
    )

    assert response.status_code == 404


def _get_task(task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    return response.json()


def _get_task_events(task_id: str) -> list[dict]:
    response = client.get(f"/api/tasks/{task_id}/events")
    assert response.status_code == 200
    return response.json()

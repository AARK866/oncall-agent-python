import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agents import OpsAgent, OpsGraphState
from app.main import app
from app.schemas import AlertSeverity, DiagnosisTaskEventType
from app.tasks import DiagnosisTaskQueue


client = TestClient(app)


def test_failed_task_can_resume_from_latest_completed_checkpoint() -> None:
    queue = DiagnosisTaskQueue()
    task = queue.submit(
        source="manual",
        question="payment-api 5xx is high",
        session_id=f"resume-after-tools-{uuid4().hex}",
        service="payment-api",
        severity=AlertSeverity.critical,
        labels={"team": "payments"},
    )
    queue.task_store.mark_running(task.task_id)

    agent = OpsAgent.create_default(
        incident_store=queue.incident_store,
        should_cancel=queue.task_store.is_cancel_requested,
    )
    nodes = agent.graph._nodes()
    state = OpsGraphState(
        question=task.question,
        session_id=task.session_id,
        thread_id=task.thread_id,
        run_id=task.run_id,
        requested_service=task.service,
        alert_severity=task.severity,
        alert_labels=task.labels,
        trigger_metadata={
            "task_id": task.task_id,
            "task_source": task.source,
            "thread_id": task.thread_id,
            "run_id": task.run_id,
        },
    )
    state.graph_trace = [name for name, _ in nodes]
    asyncio.run(agent.graph._run_local(state, nodes[:4], reason="test_partial_failure"))
    queue.task_store.mark_failed(task.task_id, "Worker crashed after execute_tools.")

    resume_response = client.post(
        f"/api/tasks/{task.task_id}/resume",
        json={
            "requested_by": "alice",
            "reason": "Continue after worker crash.",
        },
    )

    assert resume_response.status_code == 202
    resume_task = resume_response.json()
    assert resume_task["resume_of_task_id"] == task.task_id
    assert resume_task["thread_id"] == task.thread_id
    assert resume_task["run_id"] != task.run_id
    assert resume_task["trigger_metadata"]["resume"]["after_node"] == "execute_tools"

    completed_resume = _get_task(resume_task["task_id"])
    assert completed_resume["status"] == "succeeded"
    assert completed_resume["result"]["metadata"]["trigger"]["resume"]["of_task_id"] == task.task_id

    resume_events = _get_task_events(resume_task["task_id"])
    started_nodes = [
        event["data"]["node_name"]
        for event in resume_events
        if event["event_type"] == DiagnosisTaskEventType.graph_node_started.value
    ]
    assert started_nodes[0] == "retrieve_runbook"
    assert "execute_tools" not in started_nodes

    original_events = _get_task_events(task.task_id)
    resume_requested = [
        event
        for event in original_events
        if event["event_type"] == DiagnosisTaskEventType.resume_requested.value
    ]
    assert resume_requested
    assert resume_requested[-1]["data"]["new_task_id"] == resume_task["task_id"]

    resumes_response = client.get(f"/api/tasks/{task.task_id}/resumes")
    assert resumes_response.status_code == 200
    assert [task["task_id"] for task in resumes_response.json()] == [resume_task["task_id"]]


def test_succeeded_task_requires_force_to_resume() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"resume-succeeded-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "annotations": {"summary": "payment-api has elevated 5xx"},
        },
    )
    assert response.status_code == 202
    task_id = response.json()["tasks"][0]["task_id"]

    resume_response = client.post(
        f"/api/tasks/{task_id}/resume",
        json={"requested_by": "alice"},
    )

    assert resume_response.status_code == 409


def test_missing_task_resume_returns_404() -> None:
    response = client.post(
        "/api/tasks/task_missing/resume",
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

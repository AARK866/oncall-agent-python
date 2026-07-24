import json
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.langgraph_checkpointing import (
    _normalize_postgres_uri,
    create_langgraph_checkpointer,
    is_sqlite_checkpointer_available,
)
from app.agents.ops_graph import OpsGraphState, OpsGraphWorkflow
from app.config import settings
from app.main import app
from app.schemas import AlertSeverity, DiagnosisTaskEventType
from app.storage import SQLiteTaskStore
from app.tools import ToolRegistry


client = TestClient(app)


def test_alert_task_records_ops_graph_checkpoints() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"checkpoint-payment-5xx-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "labels": {"team": "payments"},
            "annotations": {"summary": "payment-api has elevated 5xx"},
        },
    )

    assert response.status_code == 202
    task_id = response.json()["tasks"][0]["task_id"]
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "waiting_review"

    task = _approve_pending_reviews(task_id)

    checkpoints_response = client.get(f"/api/tasks/{task_id}/checkpoints")
    assert checkpoints_response.status_code == 200
    checkpoints = checkpoints_response.json()
    completed_nodes = [
        checkpoint["node_name"]
        for checkpoint in checkpoints
        if checkpoint["status"] == "completed"
    ]

    assert completed_nodes == [
        "infer_service",
        "plan",
        "select_tools",
        "execute_tools",
        "retrieve_runbook",
        "build_fallback_report",
        "summarize_report",
        "build_response",
        "persist_incident",
    ]
    paused_nodes = [
        checkpoint["node_name"]
        for checkpoint in checkpoints
        if checkpoint["status"] == "paused"
    ]
    assert paused_nodes == ["human_review_gate"]
    assert task["thread_id"].startswith("thread_ag_")
    assert task["run_id"].startswith("run_")
    assert all(checkpoint["thread_id"] == task["thread_id"] for checkpoint in checkpoints)
    assert all(checkpoint["run_id"] == task["run_id"] for checkpoint in checkpoints)
    assert checkpoints[0]["state"]["session_id"].startswith("alert-")
    assert checkpoints[0]["state"]["thread_id"] == task["thread_id"]
    assert checkpoints[0]["state"]["run_id"] == task["run_id"]
    assert checkpoints[-1]["state"]["has_response"] is True
    assert task["result"]["metadata"]["graph_run"] == {
        "thread_id": task["thread_id"],
        "run_id": task["run_id"],
    }


@pytest.mark.anyio
async def test_ops_graph_records_failed_checkpoint_for_node_error(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "graph-failure.db")
    task = store.create_task(
        source="test",
        question="payment service 5xx is high",
        session_id="graph-failure-test",
        service="payment-api",
        severity=AlertSeverity.critical,
    )
    assert task.thread_id is not None
    assert task.run_id is not None
    workflow = OpsGraphWorkflow(
        tool_registry=ToolRegistry(),
        knowledge_agent=object(),
        react_loop=object(),
        plan_execute=object(),
        llm_ops_assistant=object(),
        infer_service=lambda question: "payment-api",
        build_report=lambda service, tool_results, runbook_answer: object(),
        format_report=lambda report: "",
        persist_analysis=lambda *args: None,
        checkpoint_store=store,
    )
    state = OpsGraphState(
        question="payment service 5xx is high",
        session_id="graph-failure-test",
        trigger_metadata={
            "task_id": task.task_id,
            "thread_id": task.thread_id,
            "run_id": task.run_id,
        },
    )
    state.thread_id = task.thread_id
    state.run_id = task.run_id

    async def failing_node(_: OpsGraphState) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await workflow._run_local(state, [("explode", failing_node)], reason="test")

    checkpoints = store.list_graph_checkpoints(task.task_id)
    events = store.list_events(task.task_id)

    assert [checkpoint.status for checkpoint in checkpoints] == ["started", "failed"]
    assert checkpoints[-1].thread_id == task.thread_id
    assert checkpoints[-1].run_id == task.run_id
    assert checkpoints[-1].node_name == "explode"
    assert checkpoints[-1].error == "boom"
    assert events[-1].event_type == DiagnosisTaskEventType.graph_node_failed


def test_ops_graph_uses_langgraph_memory_checkpointer(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langgraph")
    monkeypatch.setattr(settings, "ops_graph_runtime", "langgraph")
    monkeypatch.setattr(settings, "ops_graph_checkpointer", "memory")

    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"native-checkpoint-payment-5xx-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "annotations": {"summary": "payment-api has elevated 5xx"},
        },
    )

    assert response.status_code == 202
    task_id = response.json()["tasks"][0]["task_id"]
    task = _approve_pending_reviews(task_id)

    assert task["status"] == "succeeded"
    assert task["result"]["metadata"]["graph_runtime"]["used"] == "langgraph"
    assert task["result"]["metadata"]["graph_runtime"]["checkpointer_used"] == "memory"
    assert task["result"]["metadata"]["graph_runtime"]["reason"] == "native_interrupt_resume"
    assert task["result"]["metadata"]["human_review"]["resume"]["approved"] is True

    reviews_response = client.get(f"/api/tasks/{task_id}/reviews")
    assert reviews_response.status_code == 200
    reviews = reviews_response.json()
    assert len(reviews) == 1
    assert reviews[0]["status"] == "approved"

    checkpoints_response = client.get(f"/api/tasks/{task_id}/checkpoints")
    assert checkpoints_response.status_code == 200
    human_review_statuses = [
        checkpoint["status"]
        for checkpoint in checkpoints_response.json()
        if checkpoint["node_name"] == "human_review_gate"
    ]
    assert "paused" in human_review_statuses
    assert "completed" in human_review_statuses


def test_langgraph_state_payload_is_json_serializable(tmp_path) -> None:
    workflow = _minimal_workflow(tmp_path)
    state = OpsGraphState(
        question="payment service 5xx is high",
        session_id="json-safe-state",
        thread_id="thread-json-safe",
        run_id="run-json-safe",
        requested_service="payment-api",
        alert_severity=AlertSeverity.critical,
        trigger_metadata={
            "source": "test",
            "created_at": datetime(2026, 7, 21, 10, 0, 0),
        },
    )

    payload = workflow._langgraph_input(state)
    encoded = json.dumps(payload, ensure_ascii=False)
    restored = workflow._final_state_from_langgraph_result(json.loads(encoded))

    assert restored.question == state.question
    assert restored.thread_id == state.thread_id
    assert restored.alert_severity == AlertSeverity.critical
    assert restored.trigger_metadata["created_at"] == "2026-07-21T10:00:00"


def test_sqlite_langgraph_checkpointer_requires_optional_package(tmp_path) -> None:
    if is_sqlite_checkpointer_available():
        checkpointer, name = create_langgraph_checkpointer(
            "sqlite",
            sqlite_path=str(tmp_path / "langgraph-checkpoints.sqlite"),
        )
        assert checkpointer is not None
        assert name == "sqlite"
        return

    with pytest.raises(RuntimeError, match="langgraph-checkpoint-sqlite"):
        create_langgraph_checkpointer(
            "sqlite",
            sqlite_path=str(tmp_path / "langgraph-checkpoints.sqlite"),
        )


def test_postgres_checkpointer_normalizes_sqlalchemy_psycopg_url() -> None:
    assert _normalize_postgres_uri(
        "postgresql+psycopg://app:secret@postgres:5432/oncall"
    ) == "postgresql://app:secret@postgres:5432/oncall"


def test_postgres_checkpointer_rejects_non_postgres_url() -> None:
    with pytest.raises(RuntimeError, match="PostgreSQL DATABASE_URL"):
        _normalize_postgres_uri("sqlite:///app.db")


def _approve_pending_reviews(task_id: str) -> dict:
    reviews_response = client.get(f"/api/tasks/{task_id}/reviews")
    assert reviews_response.status_code == 200
    reviews = reviews_response.json()
    assert reviews

    for review in reviews:
        if review["status"] != "pending":
            continue
        approve_response = client.post(
            f"/api/reviews/{review['review_id']}/approve",
            json={"reviewer": "test", "reason": "Approved in test."},
        )
        assert approve_response.status_code == 200

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    return response.json()


def _minimal_workflow(tmp_path) -> OpsGraphWorkflow:
    return OpsGraphWorkflow(
        tool_registry=ToolRegistry(),
        knowledge_agent=object(),
        react_loop=object(),
        plan_execute=object(),
        llm_ops_assistant=object(),
        infer_service=lambda question: "payment-api",
        build_report=lambda service, tool_results, runbook_answer: object(),
        format_report=lambda report: "",
        persist_analysis=lambda *args: None,
        checkpoint_store=SQLiteTaskStore(tmp_path / "minimal-workflow.db"),
    )

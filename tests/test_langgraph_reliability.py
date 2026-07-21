from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.ops_graph import OpsGraphState, OpsGraphWorkflow
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
        "human_review_gate",
        "persist_incident",
    ]
    assert checkpoints[0]["state"]["session_id"].startswith("alert-")
    assert checkpoints[-1]["state"]["has_response"] is True


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
        trigger_metadata={"task_id": task.task_id},
    )

    async def failing_node(_: OpsGraphState) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await workflow._run_local(state, [("explode", failing_node)], reason="test")

    checkpoints = store.list_graph_checkpoints(task.task_id)
    events = store.list_events(task.task_id)

    assert [checkpoint.status for checkpoint in checkpoints] == ["started", "failed"]
    assert checkpoints[-1].node_name == "explode"
    assert checkpoints[-1].error == "boom"
    assert events[-1].event_type == DiagnosisTaskEventType.graph_node_failed

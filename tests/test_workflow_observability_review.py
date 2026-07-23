from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import (
    WorkflowEdgeDefinition,
    WorkflowGraphDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeType,
    WorkflowReviewStatus,
)
from app.storage import SQLiteWorkflowStore


client = TestClient(app)


def test_successful_workflow_run_records_events_metrics_and_audit(
    tmp_path,
    monkeypatch,
) -> None:
    app_id = _create_workflow(
        tmp_path,
        monkeypatch,
        _success_graph(),
        name="Observable Flow",
    )

    run_response = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={
            "inputs": {"service": "payment-api"},
            "requested_by": "alice",
        },
    )
    assert run_response.status_code == 200
    result = run_response.json()
    run_id = result["run_id"]
    assert result["status"] == "succeeded"
    assert run_id

    detail_response = client.get(f"/api/workflow-apps/{app_id}/runs/{run_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "succeeded"
    assert detail_response.json()["started_by"] == "alice"

    events_response = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/events"
    )
    assert events_response.status_code == 200
    events = events_response.json()
    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "run_started"
    assert event_types[-1] == "run_succeeded"
    assert event_types.count("node_started") == 2
    assert event_types.count("node_completed") == 2
    assert all(
        event["data"]["elapsed_ms"] >= 0
        for event in events
        if event["event_type"] == "node_completed"
    )

    metrics_response = client.get(
        f"/api/workflow-apps/{app_id}/runs/metrics?window_hours=24"
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["total_runs"] == 1
    assert metrics["by_status"] == {"succeeded": 1}
    assert metrics["success_rate"] == 1.0
    assert metrics["pending_reviews"] == 0

    audit_response = client.get(f"/api/workflow-apps/{app_id}/audit-events")
    assert audit_response.status_code == 200
    assert "workflow.run_started" in {
        event["action"] for event in audit_response.json()
    }


def test_human_review_approval_resumes_from_persistent_checkpoint(
    tmp_path,
    monkeypatch,
) -> None:
    app_id = _create_workflow(
        tmp_path,
        monkeypatch,
        _review_graph(),
        name="Approval Flow",
        persistent_checkpointer=True,
    )

    initial_response = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={
            "inputs": {"service": "payment-api"},
            "requested_by": "release-bot",
        },
    )
    assert initial_response.status_code == 200
    initial = initial_response.json()
    run_id = initial["run_id"]
    assert initial["status"] == "waiting_review"
    assert initial["review_requests"][0]["interrupt_id"]

    reviews_response = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/reviews"
    )
    assert reviews_response.status_code == 200
    reviews = reviews_response.json()
    assert len(reviews) == 1
    assert reviews[0]["status"] == "pending"
    review_id = reviews[0]["review_id"]

    approve_response = client.post(
        (
            f"/api/workflow-apps/{app_id}/runs/{run_id}"
            f"/reviews/{review_id}/approve"
        ),
        json={
            "reviewer": "oncall-lead",
            "reason": "Rollback plan verified.",
        },
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["review"]["status"] == "approved"
    assert approved["run"]["status"] == "succeeded"
    assert approved["result"]["status"] == "succeeded"
    assert approved["result"]["output"]["decision"]["approved"] is True
    assert approved["result"]["output"]["decision"]["reviewer"] == "oncall-lead"

    events = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/events"
    ).json()
    event_types = [event["event_type"] for event in events]
    assert "node_paused" in event_types
    assert "review_requested" in event_types
    assert "review_approved" in event_types
    assert event_types[-1] == "run_succeeded"
    assert [
        event["node_id"]
        for event in events
        if event["event_type"] == "node_completed"
    ] == ["start", "approve", "finish"]

    duplicate_response = client.post(
        (
            f"/api/workflow-apps/{app_id}/runs/{run_id}"
            f"/reviews/{review_id}/approve"
        ),
        json={"reviewer": "oncall-lead"},
    )
    assert duplicate_response.status_code == 409

    audits = client.get(f"/api/workflow-apps/{app_id}/audit-events").json()
    assert "workflow.review_approved" in {event["action"] for event in audits}


def test_human_review_rejection_terminates_run(tmp_path, monkeypatch) -> None:
    app_id = _create_workflow(
        tmp_path,
        monkeypatch,
        _review_graph(),
        name="Rejected Flow",
    )
    initial = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={"inputs": {"service": "payment-api"}},
    ).json()
    run_id = initial["run_id"]
    review = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/reviews"
    ).json()[0]

    reject_response = client.post(
        (
            f"/api/workflow-apps/{app_id}/runs/{run_id}"
            f"/reviews/{review['review_id']}/reject"
        ),
        json={"reviewer": "bob", "reason": "Change window is closed."},
    )
    assert reject_response.status_code == 200
    rejected = reject_response.json()
    assert rejected["review"]["status"] == "rejected"
    assert rejected["run"]["status"] == "rejected"
    assert rejected["result"] is None

    metrics = client.get(
        f"/api/workflow-apps/{app_id}/runs/metrics"
    ).json()
    assert metrics["by_status"]["rejected"] == 1
    assert metrics["pending_reviews"] == 0


def test_approved_review_can_resume_after_process_gap(tmp_path, monkeypatch) -> None:
    app_id = _create_workflow(
        tmp_path,
        monkeypatch,
        _review_graph(),
        name="Approval Recovery Flow",
    )
    initial = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={"inputs": {"service": "payment-api"}},
    ).json()
    run_id = initial["run_id"]
    review = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/reviews"
    ).json()[0]

    store = SQLiteWorkflowStore(settings.workflow_db_path)
    store.decide_review(
        app_id=app_id,
        run_id=run_id,
        review_id=review["review_id"],
        decision=WorkflowReviewStatus.approved,
        reviewer="recovery-reviewer",
        reason="Decision committed before process restart.",
    )
    assert store.require_run(app_id, run_id).status.value == "waiting_review"

    retry_response = client.post(
        (
            f"/api/workflow-apps/{app_id}/runs/{run_id}"
            f"/reviews/{review['review_id']}/approve"
        ),
        json={"reviewer": "recovery-reviewer"},
    )
    assert retry_response.status_code == 200
    assert retry_response.json()["run"]["status"] == "succeeded"
    assert retry_response.json()["result"]["output"]["decision"]["approved"] is True


def test_failed_node_is_persisted_for_diagnosis(tmp_path, monkeypatch) -> None:
    app_id = _create_workflow(
        tmp_path,
        monkeypatch,
        _failing_tool_graph(),
        name="Failing Flow",
    )

    run_response = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={"inputs": {}},
    )
    assert run_response.status_code == 502

    runs_response = client.get(
        f"/api/workflow-apps/{app_id}/runs?status=failed"
    )
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "WorkflowNodeExecutionError" in runs[0]["error"]

    events = client.get(
        f"/api/workflow-apps/{app_id}/runs/{runs[0]['run_id']}/events"
    ).json()
    assert [event["event_type"] for event in events][-2:] == [
        "node_failed",
        "run_failed",
    ]
    assert events[-2]["node_id"] == "missing-tool"


def _create_workflow(
    tmp_path,
    monkeypatch,
    graph: WorkflowGraphDefinition,
    name: str,
    persistent_checkpointer: bool = False,
) -> str:
    monkeypatch.setattr(settings, "workflow_db_path", str(tmp_path / f"{name}.db"))
    if persistent_checkpointer:
        monkeypatch.setattr(settings, "workflow_checkpointer", "sqlite")
        monkeypatch.setattr(
            settings,
            "workflow_checkpoint_db_path",
            str(tmp_path / f"{name}-checkpoints.db"),
        )
    create_response = client.post(
        "/api/workflow-apps",
        json={"name": name},
    )
    assert create_response.status_code == 201
    app_id = create_response.json()["app_id"]
    update_response = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={"expected_revision": 1, "graph": graph.model_dump(mode="json")},
    )
    assert update_response.status_code == 200
    return app_id


def _success_graph() -> WorkflowGraphDefinition:
    return WorkflowGraphDefinition(
        nodes=[
            WorkflowNodeDefinition(
                node_id="start",
                node_type=WorkflowNodeType.start,
                name="Start",
            ),
            WorkflowNodeDefinition(
                node_id="finish",
                node_type=WorkflowNodeType.end,
                name="Finish",
                config={"output": {"service": "${inputs.service}"}},
            ),
        ],
        edges=[
            WorkflowEdgeDefinition(
                edge_id="start-finish",
                source_node_id="start",
                target_node_id="finish",
            )
        ],
        variables={"service": {"type": "string", "required": True}},
    )


def _review_graph() -> WorkflowGraphDefinition:
    return WorkflowGraphDefinition(
        nodes=[
            WorkflowNodeDefinition(
                node_id="start",
                node_type=WorkflowNodeType.start,
                name="Start",
            ),
            WorkflowNodeDefinition(
                node_id="approve",
                node_type=WorkflowNodeType.human_review,
                name="Approve rollback",
                config={"message": "Approve rollback for ${inputs.service}?"},
            ),
            WorkflowNodeDefinition(
                node_id="finish",
                node_type=WorkflowNodeType.end,
                name="Finish",
            ),
        ],
        edges=[
            WorkflowEdgeDefinition(
                edge_id="start-review",
                source_node_id="start",
                target_node_id="approve",
            ),
            WorkflowEdgeDefinition(
                edge_id="review-finish",
                source_node_id="approve",
                target_node_id="finish",
            ),
        ],
        variables={"service": {"type": "string", "required": True}},
    )


def _failing_tool_graph() -> WorkflowGraphDefinition:
    return WorkflowGraphDefinition(
        nodes=[
            WorkflowNodeDefinition(
                node_id="start",
                node_type=WorkflowNodeType.start,
                name="Start",
            ),
            WorkflowNodeDefinition(
                node_id="missing-tool",
                node_type=WorkflowNodeType.tool,
                name="Missing Tool",
                config={"tool_name": "tool_that_does_not_exist"},
            ),
            WorkflowNodeDefinition(
                node_id="finish",
                node_type=WorkflowNodeType.end,
                name="Finish",
            ),
        ],
        edges=[
            WorkflowEdgeDefinition(
                edge_id="start-tool",
                source_node_id="start",
                target_node_id="missing-tool",
            ),
            WorkflowEdgeDefinition(
                edge_id="tool-finish",
                source_node_id="missing-tool",
                target_node_id="finish",
            ),
        ],
    )

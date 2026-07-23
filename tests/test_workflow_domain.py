from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationStatus,
    WorkflowApplicationUpdate,
    WorkflowDraftUpdate,
    WorkflowDraftRunRequest,
    WorkflowEdgeDefinition,
    WorkflowGraphDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeType,
)
from app.storage import SQLiteWorkflowStore, WorkflowRevisionConflict
from app.tools import SimpleTool, ToolRegistry
from app.workflows import (
    WorkflowCompiler,
    WorkflowNodeRuntime,
    WorkflowService,
    WorkflowValidator,
)


client = TestClient(app)


def test_workflow_store_persists_application_and_draft(tmp_path) -> None:
    db_path = tmp_path / "workflows.db"
    store = SQLiteWorkflowStore(db_path)
    application, draft = store.create_application(
        WorkflowApplicationCreate(
            name="Payment OnCall",
            description="Diagnose payment incidents.",
        )
    )

    reopened = SQLiteWorkflowStore(db_path)
    assert reopened.get_application(application.app_id) == application
    assert reopened.get_draft(application.app_id) == draft
    assert draft.revision == 1
    assert draft.graph.nodes == []


def test_workflow_draft_update_uses_optimistic_revision(tmp_path) -> None:
    store = SQLiteWorkflowStore(tmp_path / "workflows.db")
    service = WorkflowService(store)
    application = service.create(WorkflowApplicationCreate(name="Payment OnCall"))
    graph = _sample_graph()

    updated = service.update_draft(
        application.app_id,
        WorkflowDraftUpdate(expected_revision=1, graph=graph),
    )

    assert updated.revision == 2
    assert updated.graph == graph
    try:
        service.update_draft(
            application.app_id,
            WorkflowDraftUpdate(expected_revision=1, graph=graph),
        )
    except WorkflowRevisionConflict as exc:
        assert exc.expected_revision == 1
        assert exc.current_revision == 2
    else:
        raise AssertionError("Expected stale draft update to conflict")


def test_workflow_application_archive_is_filtered_by_default(tmp_path) -> None:
    service = WorkflowService(SQLiteWorkflowStore(tmp_path / "workflows.db"))
    application = service.create(WorkflowApplicationCreate(name="Legacy Flow"))
    service.update(
        application.app_id,
        WorkflowApplicationUpdate(status=WorkflowApplicationStatus.archived),
    )

    assert service.list() == []
    assert service.list(include_archived=True)[0].status == WorkflowApplicationStatus.archived


def test_workflow_control_plane_api_crud_and_revision_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "workflow_db_path", str(tmp_path / "api-workflows.db"))
    create_response = client.post(
        "/api/workflow-apps",
        json={
            "name": "Payment OnCall Agent",
            "description": "Production payment diagnosis workflow.",
        },
    )
    assert create_response.status_code == 201
    app_id = create_response.json()["app_id"]

    draft_response = client.get(f"/api/workflow-apps/{app_id}/draft")
    assert draft_response.status_code == 200
    assert draft_response.json()["revision"] == 1

    graph = _sample_graph().model_dump(mode="json")
    update_response = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={"expected_revision": 1, "graph": graph},
    )
    assert update_response.status_code == 200
    assert update_response.json()["revision"] == 2
    assert len(update_response.json()["graph"]["nodes"]) == 2

    conflict_response = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={"expected_revision": 1, "graph": graph},
    )
    assert conflict_response.status_code == 409
    assert conflict_response.json()["detail"]["current_revision"] == 2

    list_response = client.get("/api/workflow-apps")
    assert list_response.status_code == 200
    assert list_response.json()[0]["app_id"] == app_id


def test_workflow_validator_reports_structural_and_config_errors() -> None:
    graph = WorkflowGraphDefinition(
        nodes=[
            WorkflowNodeDefinition(
                node_id="start",
                node_type=WorkflowNodeType.start,
                name="Start",
            ),
            WorkflowNodeDefinition(
                node_id="broken-tool",
                node_type=WorkflowNodeType.tool,
                name="Broken Tool",
            ),
            WorkflowNodeDefinition(
                node_id="finish",
                node_type=WorkflowNodeType.end,
                name="Finish",
            ),
        ],
        edges=[
            WorkflowEdgeDefinition(
                edge_id="dangling",
                source_node_id="start",
                target_node_id="missing",
            )
        ],
    )

    report = WorkflowValidator().validate(graph)
    codes = {issue.code for issue in report.issues}

    assert report.valid is False
    assert "unknown_edge_target" in codes
    assert "tool_name_required" in codes
    assert "unreachable_node" in codes


def test_workflow_compiler_runs_parallel_tool_nodes_and_merges_outputs() -> None:
    registry = ToolRegistry()
    registry.register(
        SimpleTool(
            name="echo_service",
            description="Echo a service.",
            handler=lambda arguments: {"service": arguments["service"]},
        )
    )
    registry.register(
        SimpleTool(
            name="echo_window",
            description="Echo a time window.",
            handler=lambda arguments: {"window": arguments["window"]},
        )
    )
    compiler = WorkflowCompiler(
        runtime=WorkflowNodeRuntime(tool_registry=registry)
    )

    result = _run_async(
        compiler.compile(_parallel_tool_graph()).run(
            app_id="wfapp_test",
            draft_revision=3,
            inputs={"service": "payment-api", "window": "30m"},
            thread_id="workflow-test-thread",
        )
    )

    assert result.status.value == "succeeded"
    assert result.metadata["runtime"] == "langgraph"
    assert result.node_outputs["service-tool"]["data"]["service"] == "payment-api"
    assert result.node_outputs["window-tool"]["data"]["window"] == "30m"
    assert set(result.trace) == {"start", "service-tool", "window-tool", "finish"}
    assert result.output["service-tool"]["success"] is True


def test_workflow_validate_and_run_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "workflow_db_path", str(tmp_path / "run-api.db"))
    create_response = client.post(
        "/api/workflow-apps",
        json={"name": "Runnable Workflow"},
    )
    app_id = create_response.json()["app_id"]
    update_response = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={"expected_revision": 1, "graph": _sample_graph().model_dump(mode="json")},
    )
    assert update_response.status_code == 200

    validation_response = client.post(
        f"/api/workflow-apps/{app_id}/draft/validate"
    )
    assert validation_response.status_code == 200
    assert validation_response.json()["valid"] is True

    run_response = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={"inputs": {"question": "payment 5xx", "service": "payment-api"}},
    )
    assert run_response.status_code == 200
    data = run_response.json()
    assert data["status"] == "succeeded"
    assert data["draft_revision"] == 2
    assert data["output"] == {
        "inputs": {"question": "payment 5xx", "service": "payment-api"}
    }
    assert data["metadata"]["runtime"] == "langgraph"

    missing_input_response = client.post(
        f"/api/workflow-apps/{app_id}/draft/run",
        json={"inputs": {"question": "payment 5xx"}},
    )
    assert missing_input_response.status_code == 422
    assert "service" in missing_input_response.json()["detail"]


def test_workflow_human_review_node_compiles_to_langgraph_interrupt() -> None:
    graph = WorkflowGraphDefinition(
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
    )

    result = _run_async(
        WorkflowCompiler().compile(graph).run(
            app_id="wfapp_review",
            draft_revision=1,
            inputs={"service": "payment-api"},
        )
    )

    assert result.status.value == "waiting_review"
    assert result.review_requests[0]["node_id"] == "approve"
    assert "payment-api" in result.review_requests[0]["message"]


def _sample_graph() -> WorkflowGraphDefinition:
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


def _parallel_tool_graph() -> WorkflowGraphDefinition:
    return WorkflowGraphDefinition(
        nodes=[
            WorkflowNodeDefinition(
                node_id="start",
                node_type=WorkflowNodeType.start,
                name="Start",
            ),
            WorkflowNodeDefinition(
                node_id="service-tool",
                node_type=WorkflowNodeType.tool,
                name="Service Tool",
                config={
                    "tool_name": "echo_service",
                    "arguments": {"service": "${inputs.service}"},
                },
            ),
            WorkflowNodeDefinition(
                node_id="window-tool",
                node_type=WorkflowNodeType.tool,
                name="Window Tool",
                config={
                    "tool_name": "echo_window",
                    "arguments": {"window": "${inputs.window}"},
                },
            ),
            WorkflowNodeDefinition(
                node_id="finish",
                node_type=WorkflowNodeType.end,
                name="Finish",
            ),
        ],
        edges=[
            WorkflowEdgeDefinition(
                edge_id="start-service",
                source_node_id="start",
                target_node_id="service-tool",
            ),
            WorkflowEdgeDefinition(
                edge_id="start-window",
                source_node_id="start",
                target_node_id="window-tool",
            ),
            WorkflowEdgeDefinition(
                edge_id="service-finish",
                source_node_id="service-tool",
                target_node_id="finish",
            ),
            WorkflowEdgeDefinition(
                edge_id="window-finish",
                source_node_id="window-tool",
                target_node_id="finish",
            ),
        ],
    )


def _run_async(awaitable):
    import asyncio

    return asyncio.run(awaitable)

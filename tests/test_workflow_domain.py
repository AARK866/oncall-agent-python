from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationStatus,
    WorkflowApplicationUpdate,
    WorkflowDraftUpdate,
    WorkflowEdgeDefinition,
    WorkflowGraphDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeType,
)
from app.storage import SQLiteWorkflowStore, WorkflowRevisionConflict
from app.workflows import WorkflowService


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

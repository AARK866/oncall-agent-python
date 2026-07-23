from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowDraftUpdate,
    WorkflowEdgeDefinition,
    WorkflowGraphDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeType,
    WorkflowPublishRequest,
    WorkflowVersionRollbackRequest,
)
from app.storage import SQLiteWorkflowStore, WorkflowRevisionConflict
from app.workflows import WorkflowService, WorkflowValidationError


client = TestClient(app)


def test_published_versions_are_immutable_and_publish_is_idempotent(tmp_path) -> None:
    store = SQLiteWorkflowStore(tmp_path / "workflows.db")
    service = WorkflowService(store)
    application = service.create(WorkflowApplicationCreate(name="Payment Flow"))
    draft_v1 = service.update_draft(
        application.app_id,
        WorkflowDraftUpdate(
            expected_revision=1,
            graph=_release_graph("v1"),
        ),
    )

    version_v1 = service.publish(
        application.app_id,
        WorkflowPublishRequest(
            expected_revision=draft_v1.revision,
            published_by="alice",
            release_notes="First production release.",
        ),
    )
    duplicate = service.publish(
        application.app_id,
        WorkflowPublishRequest(
            expected_revision=draft_v1.revision,
            published_by="alice",
            release_notes="This does not create another version.",
        ),
    )
    draft_v2 = service.update_draft(
        application.app_id,
        WorkflowDraftUpdate(
            expected_revision=draft_v1.revision,
            graph=_release_graph("v2"),
        ),
    )
    version_v2 = service.publish(
        application.app_id,
        WorkflowPublishRequest(
            expected_revision=draft_v2.revision,
            published_by="bob",
        ),
    )

    reopened = SQLiteWorkflowStore(tmp_path / "workflows.db")
    versions = reopened.list_versions(application.app_id)
    persisted_v1 = reopened.get_version(application.app_id, 1)

    assert duplicate.version_id == version_v1.version_id
    assert [item.version_number for item in versions] == [2, 1]
    assert version_v2.version_number == 2
    assert persisted_v1 is not None
    assert persisted_v1.graph == _release_graph("v1")
    assert persisted_v1.graph_sha256 == version_v1.graph_sha256


def test_rollback_restores_version_to_new_draft_revision(tmp_path) -> None:
    service = WorkflowService(SQLiteWorkflowStore(tmp_path / "workflows.db"))
    application = service.create(WorkflowApplicationCreate(name="Rollback Flow"))
    draft_v1 = service.update_draft(
        application.app_id,
        WorkflowDraftUpdate(expected_revision=1, graph=_release_graph("v1")),
    )
    version_v1 = service.publish(
        application.app_id,
        WorkflowPublishRequest(
            expected_revision=draft_v1.revision,
            published_by="release-bot",
        ),
    )
    draft_v2 = service.update_draft(
        application.app_id,
        WorkflowDraftUpdate(
            expected_revision=draft_v1.revision,
            graph=_release_graph("v2"),
        ),
    )

    restored = service.rollback(
        application.app_id,
        version_v1.version_number,
        WorkflowVersionRollbackRequest(
            expected_revision=draft_v2.revision,
            requested_by="oncall",
            reason="v2 produced invalid output",
        ),
    )

    assert restored.draft.revision == draft_v2.revision + 1
    assert restored.draft.graph == version_v1.graph
    assert restored.version == version_v1
    assert service.list_versions(application.app_id) == [version_v1]

    try:
        service.rollback(
            application.app_id,
            version_v1.version_number,
            WorkflowVersionRollbackRequest(
                expected_revision=draft_v2.revision,
                requested_by="stale-client",
            ),
        )
    except WorkflowRevisionConflict as exc:
        assert exc.current_revision == restored.draft.revision
    else:
        raise AssertionError("Expected stale rollback to conflict")


def test_invalid_draft_cannot_be_published(tmp_path) -> None:
    service = WorkflowService(SQLiteWorkflowStore(tmp_path / "workflows.db"))
    application = service.create(WorkflowApplicationCreate(name="Invalid Flow"))

    try:
        service.publish(
            application.app_id,
            WorkflowPublishRequest(expected_revision=1, published_by="alice"),
        )
    except WorkflowValidationError as exc:
        assert exc.report.valid is False
    else:
        raise AssertionError("Expected invalid empty draft to be rejected")

    assert service.list_versions(application.app_id) == []


def test_workflow_version_api_runs_immutable_graph_and_rolls_back(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "workflow_db_path", str(tmp_path / "api.db"))
    create_response = client.post(
        "/api/workflow-apps",
        json={"name": "Versioned Payment Flow"},
    )
    app_id = create_response.json()["app_id"]
    update_v1 = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={
            "expected_revision": 1,
            "graph": _release_graph("v1").model_dump(mode="json"),
        },
    )
    assert update_v1.status_code == 200

    publish_response = client.post(
        f"/api/workflow-apps/{app_id}/publish",
        json={
            "expected_revision": 2,
            "published_by": "release-bot",
            "release_notes": "Stable release.",
        },
    )
    assert publish_response.status_code == 201
    assert publish_response.json()["version_number"] == 1

    update_v2 = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={
            "expected_revision": 2,
            "graph": _release_graph("v2").model_dump(mode="json"),
        },
    )
    assert update_v2.status_code == 200

    run_response = client.post(
        f"/api/workflow-apps/{app_id}/versions/1/run",
        json={"inputs": {"service": "payment-api"}, "thread_id": "release-check"},
    )
    assert run_response.status_code == 200
    assert run_response.json()["execution_source"] == "published"
    assert run_response.json()["version_number"] == 1
    assert run_response.json()["output"] == {
        "release": "v1",
        "service": "payment-api",
    }
    assert len(run_response.json()["metadata"]["graph_sha256"]) == 64

    versions_response = client.get(f"/api/workflow-apps/{app_id}/versions")
    assert versions_response.status_code == 200
    assert [item["version_number"] for item in versions_response.json()] == [1]

    rollback_response = client.post(
        f"/api/workflow-apps/{app_id}/versions/1/rollback",
        json={
            "expected_revision": 3,
            "requested_by": "oncall",
            "reason": "Restore the stable release.",
        },
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["draft"]["revision"] == 4
    assert rollback_response.json()["draft"]["graph"] == _release_graph("v1").model_dump(
        mode="json"
    )


def test_publish_api_rejects_invalid_and_stale_drafts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "workflow_db_path", str(tmp_path / "errors.db"))
    create_response = client.post(
        "/api/workflow-apps",
        json={"name": "Publish Guard Flow"},
    )
    app_id = create_response.json()["app_id"]

    invalid_response = client.post(
        f"/api/workflow-apps/{app_id}/publish",
        json={"expected_revision": 1, "published_by": "alice"},
    )
    assert invalid_response.status_code == 422
    assert invalid_response.json()["detail"]["valid"] is False

    update_response = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={
            "expected_revision": 1,
            "graph": _release_graph("v1").model_dump(mode="json"),
        },
    )
    assert update_response.status_code == 200
    stale_response = client.post(
        f"/api/workflow-apps/{app_id}/publish",
        json={"expected_revision": 1, "published_by": "alice"},
    )
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["current_revision"] == 2


def _release_graph(release: str) -> WorkflowGraphDefinition:
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
                config={
                    "output": {
                        "release": release,
                        "service": "${inputs.service}",
                    }
                },
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

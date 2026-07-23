from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


client = TestClient(app)


def test_workflow_console_and_assets_are_served() -> None:
    console = client.get("/console")
    styles = client.get("/console/assets/styles.css")
    script = client.get("/console/assets/app.js")
    mark = client.get("/console/assets/oncall-mark.svg")

    assert console.status_code == 200
    assert "OnCall Agent Control" in console.text
    assert styles.status_code == 200
    assert "--green:" in styles.text
    assert script.status_code == 200
    assert "openRunDetail" in script.text
    assert mark.status_code == 200
    assert "<svg" in mark.text


def test_console_backend_contract_covers_publish_review_and_observability(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "workflow_db_path", str(tmp_path / "console.db"))
    monkeypatch.setattr(settings, "workflow_checkpointer", "memory")

    create = client.post(
        "/api/workflow-apps",
        json={"name": "Console Acceptance"},
    )
    assert create.status_code == 201
    app_id = create.json()["app_id"]

    update = client.put(
        f"/api/workflow-apps/{app_id}/draft",
        json={"expected_revision": 1, "graph": _review_graph()},
    )
    assert update.status_code == 200
    assert update.json()["revision"] == 2

    validation = client.post(
        f"/api/workflow-apps/{app_id}/draft/validate"
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True

    publish = client.post(
        f"/api/workflow-apps/{app_id}/publish",
        json={
            "expected_revision": 2,
            "published_by": "console-test",
            "release_notes": "Console acceptance release.",
        },
    )
    assert publish.status_code == 201
    assert publish.json()["version_number"] == 1

    run = client.post(
        f"/api/workflow-apps/{app_id}/versions/1/run",
        json={
            "inputs": {"service": "payment-api"},
            "requested_by": "console-test",
        },
    )
    assert run.status_code == 200
    assert run.json()["status"] == "waiting_review"
    run_id = run.json()["run_id"]

    reviews = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/reviews"
    )
    assert reviews.status_code == 200
    review_id = reviews.json()[0]["review_id"]

    approve = client.post(
        (
            f"/api/workflow-apps/{app_id}/runs/{run_id}"
            f"/reviews/{review_id}/approve"
        ),
        json={"reviewer": "console-reviewer", "reason": "Acceptance approved."},
    )
    assert approve.status_code == 200
    assert approve.json()["run"]["status"] == "succeeded"

    metrics = client.get(f"/api/workflow-apps/{app_id}/runs/metrics")
    events = client.get(
        f"/api/workflow-apps/{app_id}/runs/{run_id}/events"
    )
    audit = client.get(f"/api/workflow-apps/{app_id}/audit-events")

    assert metrics.status_code == 200
    assert metrics.json()["success_rate"] == 1.0
    assert events.status_code == 200
    assert "review_approved" in {
        event["event_type"] for event in events.json()
    }
    assert audit.status_code == 200
    assert "workflow.review_approved" in {
        event["action"] for event in audit.json()
    }


def _review_graph() -> dict:
    return {
        "schema_version": "1.0",
        "nodes": [
            {
                "node_id": "start",
                "node_type": "start",
                "name": "Start",
                "config": {},
                "position": {"x": 80, "y": 100},
            },
            {
                "node_id": "approve",
                "node_type": "human_review",
                "name": "Approve rollback",
                "config": {
                    "title": "Approve rollback",
                    "message": "Approve rollback for ${inputs.service}?",
                },
                "position": {"x": 340, "y": 100},
            },
            {
                "node_id": "finish",
                "node_type": "end",
                "name": "Finish",
                "config": {},
                "position": {"x": 600, "y": 100},
            },
        ],
        "edges": [
            {
                "edge_id": "start-approve",
                "source_node_id": "start",
                "target_node_id": "approve",
                "condition": None,
                "priority": 0,
            },
            {
                "edge_id": "approve-finish",
                "source_node_id": "approve",
                "target_node_id": "finish",
                "condition": None,
                "priority": 0,
            },
        ],
        "variables": {
            "service": {"type": "string", "required": True}
        },
        "settings": {},
    }

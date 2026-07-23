from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.main import app


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    with TemporaryDirectory(prefix="oncall-console-acceptance-") as temporary_dir:
        settings.workflow_db_path = str(Path(temporary_dir) / "workflows.db")
        settings.workflow_checkpointer = "memory"
        settings.api_auth_enabled = False
        settings.llm_provider = "mock"

        client = TestClient(app)
        console = client.get("/console")
        checks.append(("console", console.status_code == 200, "GET /console"))

        script = client.get("/console/assets/app.js")
        checks.append(("assets", script.status_code == 200, "console JavaScript"))

        created = client.post(
            "/api/workflow-apps",
            json={"name": "Final Console Acceptance"},
        )
        app_id = created.json().get("app_id", "")
        checks.append(("create", created.status_code == 201, app_id))

        updated = client.put(
            f"/api/workflow-apps/{app_id}/draft",
            json={"expected_revision": 1, "graph": _graph()},
        )
        checks.append(("draft", updated.status_code == 200, "revision 2"))

        validated = client.post(
            f"/api/workflow-apps/{app_id}/draft/validate"
        )
        checks.append(
            (
                "validate",
                validated.status_code == 200 and validated.json().get("valid") is True,
                "valid graph",
            )
        )

        published = client.post(
            f"/api/workflow-apps/{app_id}/publish",
            json={
                "expected_revision": 2,
                "published_by": "acceptance",
                "release_notes": "Final console acceptance.",
            },
        )
        checks.append(
            (
                "publish",
                published.status_code == 201,
                "version 1",
            )
        )

        run = client.post(
            f"/api/workflow-apps/{app_id}/versions/1/run",
            json={
                "inputs": {"service": "payment-api"},
                "requested_by": "acceptance",
            },
        )
        run_data = run.json()
        run_id = run_data.get("run_id", "")
        checks.append(
            (
                "run_interrupt",
                run.status_code == 200 and run_data.get("status") == "waiting_review",
                run_id,
            )
        )

        reviews = client.get(
            f"/api/workflow-apps/{app_id}/runs/{run_id}/reviews"
        )
        review_data = reviews.json()
        review_id = review_data[0]["review_id"] if review_data else ""
        approved = client.post(
            (
                f"/api/workflow-apps/{app_id}/runs/{run_id}"
                f"/reviews/{review_id}/approve"
            ),
            json={"reviewer": "acceptance", "reason": "Approved."},
        )
        checks.append(
            (
                "review_resume",
                approved.status_code == 200
                and approved.json().get("run", {}).get("status") == "succeeded",
                review_id,
            )
        )

        events = client.get(
            f"/api/workflow-apps/{app_id}/runs/{run_id}/events"
        )
        event_types = {
            event["event_type"] for event in events.json()
        }
        checks.append(
            (
                "timeline",
                {"node_paused", "review_approved", "run_succeeded"}.issubset(
                    event_types
                ),
                f"{len(event_types)} event types",
            )
        )

        metrics = client.get(f"/api/workflow-apps/{app_id}/runs/metrics")
        audit = client.get(f"/api/workflow-apps/{app_id}/audit-events")
        checks.append(
            (
                "observability",
                metrics.status_code == 200
                and metrics.json().get("success_rate") == 1.0
                and audit.status_code == 200
                and len(audit.json()) >= 3,
                "metrics and audit",
            )
        )

    print("Workflow console acceptance")
    for name, passed, detail in checks:
        print(f"- [{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failures = sum(1 for _, passed, _ in checks if not passed)
    print(f"\nSummary: {len(checks) - failures} passed, {failures} failed")
    return 1 if failures else 0


def _graph() -> dict:
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
                    "message": "Approve rollback for ${inputs.service}?"
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


if __name__ == "__main__":
    raise SystemExit(main())

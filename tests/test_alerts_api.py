from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_alert_analyze_endpoint_triggers_ops_diagnosis() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": "alert-payment-5xx",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "labels": {"team": "payments"},
            "annotations": {
                "summary": "payment-api 5xx is above threshold",
                "description": "5xx rate stayed high for 5 minutes",
            },
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["received"] == 1
    assert data["processed"] == 1
    task = data["tasks"][0]
    assert task["status"] == "queued"

    task_detail = _get_task(task["task_id"])
    assert task_detail["status"] == "succeeded"
    assert task_detail["source"] == "api_alert"
    assert task_detail["service"] == "payment-api"
    assert task_detail["incident_id"].startswith("inc_")
    result = task_detail["result"]
    assert result["mode"] == "ops"
    assert result["metadata"]["service"] == "payment-api"
    assert result["metadata"]["trigger"]["source"] == "api_alert"
    assert result["metadata"]["trigger"]["severity"] == "critical"

    event_types = _get_task_event_types(task["task_id"])
    assert event_types[0] == "queued"
    assert "running" in event_types
    assert "tool_result" in event_types
    assert "retrieved_docs" in event_types
    assert "incident_persisted" in event_types
    assert event_types[-1] == "succeeded"


def test_alertmanager_webhook_processes_only_firing_alerts() -> None:
    response = client.post(
        "/api/alerts/alertmanager",
        json={
            "version": "4",
            "groupKey": "{}:{alertname=\"High5xxRate\"}",
            "status": "firing",
            "receiver": "oncall-agent",
            "commonLabels": {"service": "payment-api", "severity": "critical"},
            "commonAnnotations": {"summary": "payment-api has elevated 5xx"},
            "externalURL": "http://localhost:9093",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "High5xxRate"},
                    "annotations": {"description": "5xx rate is above 5%"},
                    "startsAt": "2026-07-06T10:00:00Z",
                    "generatorURL": "http://localhost:9090/graph",
                    "fingerprint": "payment-5xx-fingerprint",
                },
                {
                    "status": "resolved",
                    "labels": {"alertname": "OldLatencyAlert"},
                    "annotations": {"description": "already resolved"},
                    "startsAt": "2026-07-06T09:00:00Z",
                    "endsAt": "2026-07-06T09:10:00Z",
                    "fingerprint": "resolved-fingerprint",
                },
            ],
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["received"] == 2
    assert data["processed"] == 1
    assert data["metadata"]["source"] == "alertmanager"
    assert data["metadata"]["ignored"] == 1
    task = data["tasks"][0]
    task_detail = _get_task(task["task_id"])
    assert task_detail["status"] == "succeeded"
    assert task_detail["source"] == "alertmanager"
    result = task_detail["result"]
    assert result["metadata"]["service"] == "payment-api"
    assert result["metadata"]["trigger"]["source"] == "alertmanager"
    assert result["metadata"]["trigger"]["alert_id"] == "payment-5xx-fingerprint"


def test_alertmanager_webhook_ignores_resolved_only_payload() -> None:
    response = client.post(
        "/api/alerts/alertmanager",
        json={
            "status": "resolved",
            "receiver": "oncall-agent",
            "alerts": [
                {
                    "status": "resolved",
                    "labels": {"alertname": "High5xxRate", "service": "payment-api"},
                    "annotations": {"summary": "already resolved"},
                    "startsAt": "2026-07-06T09:00:00Z",
                    "endsAt": "2026-07-06T09:10:00Z",
                }
            ],
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["received"] == 1
    assert data["processed"] == 0
    assert data["results"] == []
    assert data["tasks"] == []


def test_task_endpoint_returns_404_for_missing_task() -> None:
    response = client.get("/api/tasks/task_missing")

    assert response.status_code == 404


def test_task_events_endpoint_returns_404_for_missing_task() -> None:
    response = client.get("/api/tasks/task_missing/events")

    assert response.status_code == 404


def _get_task(task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    return response.json()


def _get_task_event_types(task_id: str) -> list[str]:
    response = client.get(f"/api/tasks/{task_id}/events")
    assert response.status_code == 200
    return [event["event_type"] for event in response.json()]

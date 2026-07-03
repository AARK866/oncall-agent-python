from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_endpoint_returns_ops_response() -> None:
    response = client.post(
        "/api/chat",
        json={
            "message": "payment service 5xx error rate is high",
            "session_id": "api-test",
            "mode": "auto",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "ops"
    assert data["metadata"]["service"] == "payment-api"
    assert data["metadata"]["incident_id"].startswith("inc_")
    assert data["answer"]


def test_incident_analyze_and_history_endpoints() -> None:
    analyze_response = client.post(
        "/api/incidents/analyze",
        json={
            "message": "payment service 5xx error rate is high",
            "session_id": "incident-api-test",
            "mode": "ops",
        },
    )

    assert analyze_response.status_code == 200
    analyze_data = analyze_response.json()
    incident_id = analyze_data["metadata"]["incident_id"]
    diagnosis_id = analyze_data["metadata"]["diagnosis_id"]

    list_response = client.get("/api/incidents?limit=50")
    assert list_response.status_code == 200
    incident_ids = {item["incident_id"] for item in list_response.json()}
    assert incident_id in incident_ids

    detail_response = client.get(f"/api/incidents/{incident_id}")
    assert detail_response.status_code == 200
    detail_data = detail_response.json()
    assert detail_data["incident"]["incident_id"] == incident_id
    assert detail_data["latest_diagnosis"]["diagnosis_id"] == diagnosis_id


def test_get_incident_returns_404_for_missing_id() -> None:
    response = client.get("/api/incidents/inc_missing")

    assert response.status_code == 404

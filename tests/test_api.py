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
            "message": "payment 服务 5xx 升高怎么办",
            "session_id": "api-test",
            "mode": "auto",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "ops"
    assert data["metadata"]["service"] == "payment-api"
    assert "诊断结论" in data["answer"]

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


def test_knowledge_stats_and_documents_endpoints() -> None:
    stats_response = client.get("/api/knowledge/stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["document_count"] >= 2
    assert stats["chunk_count"] >= 2
    assert stats["retriever_mode"] in {"keyword", "vector", "hybrid"}

    documents_response = client.get("/api/knowledge/documents")
    assert documents_response.status_code == 200
    documents = documents_response.json()
    assert documents
    assert {document["doc_id"] for document in documents} >= {"payment_5xx.md", "order_timeout.md"}

    detail_response = client.get("/api/knowledge/documents/payment_5xx.md")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["doc_id"] == "payment_5xx.md"
    assert "5xx" in detail["content"]


def test_knowledge_search_endpoint() -> None:
    response = client.post(
        "/api/knowledge/search",
        json={
            "query": "payment service 5xx error rate",
            "top_k": 2,
            "service": "payment-api",
            "incident_type": "5xx",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "payment service 5xx error rate"
    assert data["metadata"]["retrieved_count"] > 0
    assert data["results"][0]["metadata"]["services"] == ["payment-api"]


def test_knowledge_document_returns_404_for_missing_doc() -> None:
    response = client.get("/api/knowledge/documents/missing.md")

    assert response.status_code == 404

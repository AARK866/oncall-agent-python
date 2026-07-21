from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_alert_diagnosis_creates_pending_human_review_for_rollback() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"review-payment-5xx-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "labels": {"team": "payments"},
            "annotations": {"summary": "payment-api has elevated 5xx after a deployment"},
        },
    )

    assert response.status_code == 202
    task_id = response.json()["tasks"][0]["task_id"]
    task = client.get(f"/api/tasks/{task_id}").json()
    human_review = task["result"]["metadata"]["human_review"]

    assert human_review["required"] is True
    assert human_review["status"] == "pending"
    assert human_review["review_ids"]

    reviews_response = client.get(f"/api/tasks/{task_id}/reviews")
    assert reviews_response.status_code == 200
    reviews = reviews_response.json()
    assert len(reviews) == 1
    assert reviews[0]["status"] == "pending"
    assert "回滚" in reviews[0]["proposed_actions"][0]


def test_human_review_can_be_approved() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"approve-review-payment-5xx-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "annotations": {"summary": "payment-api has elevated 5xx after a deployment"},
        },
    )
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = client.get(f"/api/tasks/{task_id}/reviews").json()[0]["review_id"]

    approve_response = client.post(
        f"/api/reviews/{review_id}/approve",
        json={"reviewer": "alice", "reason": "Deployment owner confirmed rollback plan."},
    )

    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["status"] == "approved"
    assert approved["reviewer"] == "alice"
    assert approved["decided_at"] is not None


def test_human_review_can_be_rejected() -> None:
    response = client.post(
        "/api/alerts/analyze",
        json={
            "alert_id": f"reject-review-payment-5xx-{uuid4().hex}",
            "title": "High5xxRate",
            "service": "payment-api",
            "severity": "critical",
            "annotations": {"summary": "payment-api has elevated 5xx after a deployment"},
        },
    )
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = client.get(f"/api/tasks/{task_id}/reviews").json()[0]["review_id"]

    reject_response = client.post(
        f"/api/reviews/{review_id}/reject",
        json={"reviewer": "bob", "reason": "Need database owner confirmation first."},
    )

    assert reject_response.status_code == 200
    rejected = reject_response.json()
    assert rejected["status"] == "rejected"
    assert rejected["reviewer"] == "bob"


def test_missing_human_review_returns_404() -> None:
    response = client.get("/api/reviews/review_missing")

    assert response.status_code == 404

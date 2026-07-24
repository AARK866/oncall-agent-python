from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.security_context import AuthPrincipal, principal_scope
from app.tasks.dispatcher import TaskDispatcher


client = TestClient(app)


class FakeCelery:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_task(self, name: str, **kwargs) -> None:
        self.calls.append({"name": name, **kwargs})


class FakeCoordinator:
    def reserve_dispatch(self, task_kind: str, task_id: str):
        return type(
            "Reservation",
            (),
            {"key": f"{task_kind}:{task_id}", "token": "token"},
        )()

    def release_dispatch(self, _reservation) -> None:
        return None


def test_jwt_identity_exposes_tenant_roles_and_permissions(monkeypatch) -> None:
    _configure_hs256(monkeypatch)
    token = _token(roles=["viewer"], tenant_id="tenant-blue")

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["subject"] == "user-123"
    assert response.json()["tenant_id"] == "tenant-blue"
    assert response.json()["roles"] == ["viewer"]
    assert "tasks:read" in response.json()["permissions"]


def test_viewer_can_read_but_cannot_mutate_tasks(monkeypatch) -> None:
    _configure_hs256(monkeypatch)
    token = _token(roles=["viewer"], tenant_id="tenant-blue")
    headers = {"Authorization": f"Bearer {token}"}

    read_response = client.get("/api/tasks", headers=headers)
    write_response = client.post(
        "/api/tasks/recover-stale",
        json={},
        headers=headers,
    )

    assert read_response.status_code == 200
    assert write_response.status_code == 403
    assert write_response.json()["detail"] == (
        "Missing required permission: tasks:write"
    )


def test_jwt_rejects_wrong_audience_and_missing_tenant(monkeypatch) -> None:
    _configure_hs256(monkeypatch)
    wrong_audience = _token(
        roles=["sre"],
        tenant_id="tenant-blue",
        audience="another-service",
    )
    missing_tenant = _token(roles=["sre"], tenant_id=None)

    wrong_response = client.get(
        "/api/tasks",
        headers={"Authorization": f"Bearer {wrong_audience}"},
    )
    missing_response = client.get(
        "/api/tasks",
        headers={"Authorization": f"Bearer {missing_tenant}"},
    )

    assert wrong_response.status_code == 401
    assert missing_response.status_code == 401


def test_celery_message_captures_request_tenant() -> None:
    celery = FakeCelery()
    dispatcher = TaskDispatcher(
        mode="celery",
        coordinator=FakeCoordinator(),
        celery_application=celery,
    )
    principal = AuthPrincipal(
        subject="user-123",
        tenant_id="tenant-green",
        roles=frozenset({"sre"}),
        permissions=frozenset({"*"}),
        source="jwt",
    )

    with principal_scope(principal):
        dispatcher.dispatch_diagnosis("task-tenant")

    assert celery.calls[0]["args"] == ["task-tenant", "tenant-green"]


def _configure_hs256(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(
        settings,
        "jwt_secret",
        "unit-test-jwt-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(settings, "jwt_algorithms", "HS256")
    monkeypatch.setattr(settings, "jwt_issuer", "https://identity.example.test")
    monkeypatch.setattr(settings, "jwt_audience", "oncall-agent")


def _token(
    *,
    roles: list[str],
    tenant_id: str | None,
    audience: str = "oncall-agent",
) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-123",
        "roles": roles,
        "iss": "https://identity.example.test",
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    return jwt.encode(
        claims,
        "unit-test-jwt-secret-at-least-32-bytes",
        algorithm="HS256",
    )

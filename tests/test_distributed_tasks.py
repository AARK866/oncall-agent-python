from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import DiagnosisTaskStatus
from app.storage import SQLiteTaskStore
from app.tasks.celery_app import celery_app
from app.tasks.dispatcher import TaskDispatchError, TaskDispatcher
from app.tasks.redis_coordination import RedisCoordinator
from app.tasks.worker_tasks import (
    cleanup_audit_events,
    health_ping,
    recover_stale_tasks,
)


client = TestClient(app)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.queues: dict[str, list[str]] = {}

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        ex: int,
    ) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script: str, key_count: int, key: str, token: str) -> int:
        del script, key_count
        if self.values.get(key) != token:
            return 0
        del self.values[key]
        return 1

    def ping(self) -> bool:
        return True

    def llen(self, queue: str) -> int:
        return len(self.queues.get(queue, []))


class FakeCelery:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs) -> None:
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.calls.append({"name": name, **kwargs})


class FakeBackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, function, *args) -> None:
        self.calls.append((function, args))


def test_local_dispatch_uses_background_task() -> None:
    background_tasks = FakeBackgroundTasks()

    receipt = TaskDispatcher(mode="local").dispatch_diagnosis(
        "task-local",
        background_tasks,
    )

    assert receipt.scheduled is True
    assert receipt.mode == "local"
    assert background_tasks.calls[0][1] == ("task-local",)


def test_celery_dispatch_is_deduplicated_in_redis() -> None:
    coordinator = RedisCoordinator(FakeRedis())
    celery = FakeCelery()
    dispatcher = TaskDispatcher(
        mode="celery",
        coordinator=coordinator,
        celery_application=celery,
    )

    first = dispatcher.dispatch_diagnosis("task-redis")
    duplicate = dispatcher.dispatch_diagnosis("task-redis")

    assert first.scheduled is True
    assert first.broker_task_id is not None
    assert duplicate.scheduled is False
    assert duplicate.duplicate is True
    assert len(celery.calls) == 1
    assert celery.calls[0]["queue"] == "diagnosis"


def test_failed_broker_publish_releases_dispatch_reservation() -> None:
    fake_redis = FakeRedis()
    coordinator = RedisCoordinator(fake_redis)
    failing_dispatcher = TaskDispatcher(
        mode="celery",
        coordinator=coordinator,
        celery_application=FakeCelery(fail=True),
    )

    with pytest.raises(TaskDispatchError):
        failing_dispatcher.dispatch_knowledge_ingestion("knowledge-task")

    successful = TaskDispatcher(
        mode="celery",
        coordinator=coordinator,
        celery_application=FakeCelery(),
    ).dispatch_knowledge_ingestion("knowledge-task")
    assert successful.scheduled is True


def test_execution_lease_allows_only_one_worker() -> None:
    coordinator = RedisCoordinator(FakeRedis())

    with coordinator.execution_lease("diagnosis", "task-lock") as first:
        with coordinator.execution_lease("diagnosis", "task-lock") as second:
            assert first.acquired is True
            assert second.acquired is False

    with coordinator.execution_lease("diagnosis", "task-lock") as third:
        assert third.acquired is True


def test_database_claim_allows_only_one_diagnosis_worker(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "claim.db")
    task = store.create_task(
        source="test",
        question="claim once",
        session_id="claim-test",
    )

    first = store.claim_for_execution(task.task_id)
    duplicate = store.claim_for_execution(task.task_id)

    assert first is not None
    assert first.status == DiagnosisTaskStatus.running
    assert duplicate is None


def test_stale_recovery_resumes_timed_out_tasks(monkeypatch) -> None:
    timed_out = SimpleNamespace(
        task_id="timed-out-task",
        status=DiagnosisTaskStatus.timed_out,
    )
    resumed = SimpleNamespace(task_id="resumed-task")
    dispatched: list[str] = []

    class FakeQueue:
        def recover_stale_tasks(self, **kwargs):
            assert kwargs["requested_by"] == "celery-beat"
            return [timed_out]

        def resume(self, task_id: str, **kwargs):
            assert task_id == "timed-out-task"
            assert kwargs["requested_by"] == "celery-beat"
            return resumed

    class FakeDispatcher:
        def dispatch_diagnosis(self, task_id: str) -> None:
            dispatched.append(task_id)

    monkeypatch.setattr(settings, "task_queue_mode", "celery")
    monkeypatch.setattr(settings, "stale_task_auto_resume_enabled", True)
    monkeypatch.setattr(
        "app.tasks.worker_tasks.DiagnosisTaskQueue",
        FakeQueue,
    )
    monkeypatch.setattr(
        "app.tasks.worker_tasks.TaskDispatcher",
        FakeDispatcher,
    )

    result = recover_stale_tasks.run()

    assert result["resumed_task_ids"] == ["resumed-task"]
    assert dispatched == ["resumed-task"]


def test_celery_routes_and_health_task_are_registered() -> None:
    assert (
        celery_app.conf.task_routes["oncall.tasks.run_diagnosis"]["queue"]
        == "diagnosis"
    )
    assert health_ping.run("distributed-probe") == {
        "status": "ok",
        "probe": "distributed-probe",
    }
    assert (
        celery_app.conf.task_routes[
            "oncall.maintenance.cleanup_audit"
        ]["queue"]
        == "maintenance"
    )


def test_audit_cleanup_uses_configured_retention(monkeypatch) -> None:
    class FakeAuditStore:
        def delete_expired(self):
            return 7

    monkeypatch.setattr(
        "app.tasks.worker_tasks.AuditStore.from_settings",
        lambda: FakeAuditStore(),
    )
    monkeypatch.setattr(settings, "audit_retention_days", 90)

    assert cleanup_audit_events.run() == {
        "deleted": 7,
        "retention_days": 90,
    }


def test_local_queue_health_does_not_require_redis(monkeypatch) -> None:
    monkeypatch.setattr(settings, "task_queue_mode", "local")

    response = client.get("/health/queue")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "local",
        "broker": "disabled",
    }

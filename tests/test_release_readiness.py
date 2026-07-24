import httpx
import pytest

from app.main import app
from scripts.run_failure_drills import drill_plan
from scripts.run_load_test import (
    LoadSample,
    LoadTestConfig,
    run_load_test,
    summarize_samples,
)
from scripts.run_release_gate import run_release_gate


def test_load_summary_passes_when_sla_thresholds_are_met() -> None:
    summary = summarize_samples(
        [
            LoadSample(duration_ms=10, status_code=200),
            LoadSample(duration_ms=20, status_code=200),
            LoadSample(duration_ms=30, status_code=200),
        ],
        elapsed_seconds=0.1,
        max_error_rate=0,
        max_p95_ms=50,
        min_requests_per_second=10,
    )

    assert summary.passed is True
    assert summary.error_rate == 0
    assert summary.p95_ms == 30
    assert summary.requests_per_second == 30


def test_load_summary_fails_when_error_budget_is_exceeded() -> None:
    summary = summarize_samples(
        [
            LoadSample(duration_ms=10, status_code=200),
            LoadSample(duration_ms=15, status_code=500),
        ],
        elapsed_seconds=0.1,
        max_error_rate=0.01,
        max_p95_ms=50,
    )

    assert summary.passed is False
    assert summary.error_rate == 0.5
    assert summary.status_codes == {"200": 1, "500": 1}


@pytest.mark.anyio
async def test_in_process_load_test_exercises_real_fastapi_stack() -> None:
    summary = await run_load_test(
        LoadTestConfig(
            target_url="http://testserver",
            requests=30,
            concurrency=6,
            warmup_requests=2,
            max_error_rate=0,
            max_p95_ms=2000,
        ),
        transport=httpx.ASGITransport(app=app),
    )

    assert summary.passed is True
    assert summary.requests == 30
    assert summary.failed == 0


@pytest.mark.anyio
async def test_release_gate_checks_runtime_contracts() -> None:
    checks = await run_release_gate(
        target_url="http://testserver",
        transport=httpx.ASGITransport(app=app),
        load_requests=20,
        load_concurrency=5,
        max_error_rate=0,
        max_p95_ms=2000,
    )

    assert checks
    assert all(check.status == "PASS" for check in checks)
    assert {check.name for check in checks} == {
        "liveness",
        "trace_propagation",
        "database_readiness",
        "queue_readiness",
        "metrics",
        "load_sla",
    }


def test_failure_drill_plan_orders_recoverable_dependencies() -> None:
    plan = drill_plan("all")

    assert [drill.name for drill in plan] == [
        "redis",
        "worker",
        "postgres",
    ]
    assert plan[0].outage_path == "/health/queue"
    assert plan[-1].outage_path == "/health/database"

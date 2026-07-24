import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.security import validate_production_security
from scripts.run_load_test import LoadTestConfig, run_load_test


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: str
    detail: str
    metadata: dict[str, Any] | None = None


async def run_release_gate(
    *,
    target_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
    validate_config: bool = False,
    require_auth: bool = False,
    skip_database: bool = False,
    skip_queue: bool = False,
    skip_metrics: bool = False,
    skip_load: bool = False,
    load_requests: int = 200,
    load_concurrency: int = 20,
    max_error_rate: float = 0.01,
    max_p95_ms: float = 500.0,
) -> list[GateCheck]:
    checks: list[GateCheck] = []
    if validate_config:
        missing = validate_production_security()
        checks.append(
            GateCheck(
                name="production_config",
                status="PASS" if not missing else "FAIL",
                detail=(
                    "production configuration is valid"
                    if not missing
                    else f"missing or invalid: {', '.join(missing)}"
                ),
            )
        )

    async with httpx.AsyncClient(
        base_url=target_url.rstrip("/"),
        timeout=10,
        transport=transport,
    ) as client:
        checks.append(await _health_check(client))
        checks.append(await _trace_check(client))
        if not skip_database:
            checks.append(await _database_check(client))
        if not skip_queue:
            checks.append(await _queue_check(client))
        if not skip_metrics:
            checks.append(await _metrics_check(client))
        if require_auth:
            checks.append(await _auth_check(client))

    if not skip_load:
        load_summary = await run_load_test(
            LoadTestConfig(
                target_url=target_url,
                requests=load_requests,
                concurrency=load_concurrency,
                max_error_rate=max_error_rate,
                max_p95_ms=max_p95_ms,
                api_token=settings.api_token,
            ),
            transport=transport,
        )
        checks.append(
            GateCheck(
                name="load_sla",
                status="PASS" if load_summary.passed else "FAIL",
                detail=(
                    f"error_rate={load_summary.error_rate:.2%}, "
                    f"p95={load_summary.p95_ms:.2f}ms, "
                    f"rps={load_summary.requests_per_second:.2f}"
                ),
                metadata=load_summary.to_dict(),
            )
        )
    return checks


async def _health_check(client: httpx.AsyncClient) -> GateCheck:
    try:
        response = await client.get("/health")
        data = response.json()
        passed = response.status_code == 200 and data.get("status") == "ok"
        return GateCheck(
            "liveness",
            "PASS" if passed else "FAIL",
            f"status_code={response.status_code}, status={data.get('status')}",
        )
    except Exception as exc:
        return GateCheck("liveness", "FAIL", exc.__class__.__name__)


async def _database_check(client: httpx.AsyncClient) -> GateCheck:
    try:
        response = await client.get("/health/database")
        data = response.json()
        passed = response.status_code == 200 and data.get("status") == "ok"
        return GateCheck(
            "database_readiness",
            "PASS" if passed else "FAIL",
            f"status_code={response.status_code}, status={data.get('status')}",
        )
    except Exception as exc:
        return GateCheck(
            "database_readiness",
            "FAIL",
            exc.__class__.__name__,
        )


async def _queue_check(client: httpx.AsyncClient) -> GateCheck:
    try:
        response = await client.get("/health/queue")
        data = response.json()
        passed = response.status_code == 200 and data.get("status") == "ok"
        return GateCheck(
            "queue_readiness",
            "PASS" if passed else "FAIL",
            f"status_code={response.status_code}, status={data.get('status')}",
        )
    except Exception as exc:
        return GateCheck("queue_readiness", "FAIL", exc.__class__.__name__)


async def _trace_check(client: httpx.AsyncClient) -> GateCheck:
    trace_id = f"release-gate-{uuid4().hex}"
    try:
        response = await client.get(
            "/health",
            headers={"X-Trace-ID": trace_id},
        )
        returned = response.headers.get("X-Trace-ID")
        return GateCheck(
            "trace_propagation",
            "PASS" if returned == trace_id else "FAIL",
            f"trace_id_preserved={returned == trace_id}",
        )
    except Exception as exc:
        return GateCheck(
            "trace_propagation",
            "FAIL",
            exc.__class__.__name__,
        )


async def _metrics_check(client: httpx.AsyncClient) -> GateCheck:
    headers: dict[str, str] = {}
    if settings.metrics_auth_token:
        headers["Authorization"] = f"Bearer {settings.metrics_auth_token}"
    try:
        response = await client.get("/metrics", headers=headers)
        passed = (
            response.status_code == 200
            and "oncall_http_requests_total" in response.text
        )
        return GateCheck(
            "metrics",
            "PASS" if passed else "FAIL",
            f"status_code={response.status_code}, metric_found={passed}",
        )
    except Exception as exc:
        return GateCheck("metrics", "FAIL", exc.__class__.__name__)


async def _auth_check(client: httpx.AsyncClient) -> GateCheck:
    try:
        response = await client.get("/api/tasks")
        passed = response.status_code in {401, 403}
        return GateCheck(
            "authentication_fail_closed",
            "PASS" if passed else "FAIL",
            f"unauthenticated_status_code={response.status_code}",
        )
    except Exception as exc:
        return GateCheck(
            "authentication_fail_closed",
            "FAIL",
            exc.__class__.__name__,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the final production release gate."
    )
    parser.add_argument("--target-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--self-host",
        action="store_true",
        help="Start a temporary local Uvicorn process for the gate.",
    )
    parser.add_argument("--validate-production-config", action="store_true")
    parser.add_argument("--require-auth", action="store_true")
    parser.add_argument("--skip-database", action="store_true")
    parser.add_argument("--skip-queue", action="store_true")
    parser.add_argument("--skip-metrics", action="store_true")
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--load-requests", type=int, default=200)
    parser.add_argument("--load-concurrency", type=int, default=20)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _print_checks(checks: list[GateCheck], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(check) for check in checks], indent=2))
        return
    print("OnCall Agent production release gate")
    for check in checks:
        print(f"- [{check.status}] {check.name}: {check.detail}")
    passed = sum(check.status == "PASS" for check in checks)
    failed = sum(check.status == "FAIL" for check in checks)
    print("")
    print(f"Summary: {passed} passed, {failed} failed")


async def main() -> int:
    args = _parse_args()
    server = _start_self_hosted_server(args.target_url) if args.self_host else None
    try:
        if server is not None:
            await _wait_until_reachable(args.target_url, server)
        checks = await run_release_gate(
            target_url=args.target_url,
            validate_config=args.validate_production_config,
            require_auth=args.require_auth,
            skip_database=args.skip_database,
            skip_queue=args.skip_queue,
            skip_metrics=args.skip_metrics,
            skip_load=args.skip_load,
            load_requests=args.load_requests,
            load_concurrency=args.load_concurrency,
            max_error_rate=args.max_error_rate,
            max_p95_ms=args.max_p95_ms,
        )
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    _print_checks(checks, as_json=args.json)
    return 0 if all(check.status == "PASS" for check in checks) else 1


def _start_self_hosted_server(target_url: str) -> subprocess.Popen:
    parsed = urlsplit(target_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("--self-host requires a local HTTP target URL")
    port = parsed.port or 80
    env = os.environ.copy()
    env["LOG_LEVEL"] = "WARNING"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _wait_until_reachable(
    target_url: str,
    server: subprocess.Popen,
    timeout_seconds: float = 20,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    async with httpx.AsyncClient(base_url=target_url, timeout=1) as client:
        while asyncio.get_running_loop().time() < deadline:
            if server.poll() is not None:
                raise RuntimeError(
                    f"self-hosted Uvicorn exited with code {server.returncode}"
                )
            try:
                response = await client.get("/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError("self-hosted Uvicorn did not become ready")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

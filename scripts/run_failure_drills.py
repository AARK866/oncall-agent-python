import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class FailureDrill:
    name: str
    service: str
    outage_path: str
    expected_during_outage: str


DRILLS = {
    "postgres": FailureDrill(
        name="postgres",
        service="postgres",
        outage_path="/health/database",
        expected_during_outage="unhealthy",
    ),
    "redis": FailureDrill(
        name="redis",
        service="redis",
        outage_path="/health/queue",
        expected_during_outage="unhealthy",
    ),
    "worker": FailureDrill(
        name="worker",
        service="worker",
        outage_path="/health",
        expected_during_outage="healthy",
    ),
}


def drill_plan(scenario: str) -> list[FailureDrill]:
    if scenario == "all":
        return [DRILLS[name] for name in ("redis", "worker", "postgres")]
    return [DRILLS[scenario]]


def execute_drill(
    drill: FailureDrill,
    *,
    compose_file: Path,
    target_url: str,
    timeout_seconds: int,
) -> bool:
    print(f"[RUN] {drill.name}: stopping {drill.service}", flush=True)
    _compose(compose_file, "stop", drill.service)
    outage_ok = False
    recovery_ok = False
    try:
        outage_ok = _wait_for_state(
            target_url + drill.outage_path,
            expected=drill.expected_during_outage,
            timeout_seconds=timeout_seconds,
        )
        liveness_ok = _wait_for_state(
            target_url + "/health",
            expected="healthy",
            timeout_seconds=timeout_seconds,
        )
        print(
            f"[{'PASS' if outage_ok else 'FAIL'}] outage behavior: "
            f"expected {drill.expected_during_outage}",
            flush=True,
        )
        print(
            f"[{'PASS' if liveness_ok else 'FAIL'}] API liveness during outage",
            flush=True,
        )
    finally:
        print(f"[RECOVER] starting {drill.service}", flush=True)
        _compose(compose_file, "start", drill.service)
        recovery_ok = _wait_for_state(
            target_url + "/health/database",
            expected="healthy",
            timeout_seconds=timeout_seconds,
        )
        print(
            f"[{'PASS' if recovery_ok else 'FAIL'}] recovery",
            flush=True,
        )
    return outage_ok and liveness_ok and recovery_ok


def _wait_for_state(
    url: str,
    *,
    expected: str,
    timeout_seconds: int,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        healthy = _is_healthy(url)
        if expected == "healthy" and healthy:
            return True
        if expected == "unhealthy" and not healthy:
            return True
        time.sleep(1)
    return False


def _is_healthy(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=3)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _compose(compose_file: Path, action: str, service: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            action,
            service,
        ],
        check=True,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recoverable Docker Compose failure drills."
    )
    parser.add_argument(
        "--scenario",
        choices=["redis", "worker", "postgres", "all"],
        default="all",
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path("docker-compose.yml"),
    )
    parser.add_argument("--target-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually stop services. Without this flag only print the plan.",
    )
    return parser.parse_args()


def _print_plan(plan: list[FailureDrill]) -> None:
    print("OnCall Agent failure drill plan")
    for drill in plan:
        print(
            f"- {drill.name}: stop {drill.service}; "
            f"{drill.outage_path} should be "
            f"{drill.expected_during_outage}; always restart service"
        )
    print("- dry run only; pass --execute to perform the drill")


def main() -> int:
    args = _parse_args()
    plan = drill_plan(args.scenario)
    if not args.execute:
        _print_plan(plan)
        return 0

    results = [
        execute_drill(
            drill,
            compose_file=args.compose_file,
            target_url=args.target_url.rstrip("/"),
            timeout_seconds=args.timeout,
        )
        for drill in plan
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

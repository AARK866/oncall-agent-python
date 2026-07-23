from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.tasks.dispatcher import HEALTH_TASK_NAME
from app.tasks.redis_coordination import RedisCoordinator


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Redis coordination and a live Celery worker."
    )
    parser.add_argument("--redis-url", help="Override REDIS_URL.")
    parser.add_argument(
        "--result-backend",
        help="Override CELERY_RESULT_BACKEND.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if args.redis_url:
        settings.redis_url = args.redis_url
    if args.result_backend:
        settings.celery_result_backend = args.result_backend

    checks: list[tuple[str, bool, str]] = []
    coordinator = RedisCoordinator()
    probe_id = f"probe-{uuid4().hex}"

    try:
        checks.append(("redis", coordinator.ping(), "PING"))

        first = coordinator.reserve_dispatch("health", probe_id)
        duplicate = coordinator.reserve_dispatch("health", probe_id)
        checks.append(
            (
                "dispatch_dedup",
                first is not None and duplicate is None,
                "duplicate suppressed",
            )
        )
        if first is not None:
            coordinator.release_dispatch(first)

        with coordinator.execution_lease("health", probe_id) as lease:
            with coordinator.execution_lease("health", probe_id) as duplicate_lease:
                lease_ok = lease.acquired and not duplicate_lease.acquired
        checks.append(("execution_lease", lease_ok, "single owner"))

        from app.tasks.celery_app import celery_app

        result = celery_app.send_task(
            HEALTH_TASK_NAME,
            args=[probe_id],
            queue="maintenance",
        ).get(timeout=args.timeout, disable_sync_subtasks=False)
        worker_ok = result == {"status": "ok", "probe": probe_id}
        checks.append(("celery_worker", worker_ok, str(result)))

        depths = coordinator.queue_depths()
        checks.append(("queue_metrics", True, str(depths)))
    except Exception as exc:
        checks.append(
            (
                "unexpected_error",
                False,
                f"{type(exc).__name__}: {exc}",
            )
        )

    print("Distributed task queue acceptance")
    for name, passed, detail in checks:
        print(f"- [{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failures = sum(not passed for _, passed, _ in checks)
    print(f"\nSummary: {len(checks) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

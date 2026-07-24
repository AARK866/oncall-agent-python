import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from scripts.check_real_incident_remediation import (
    _agent_headers,
    _approve_reviews,
    _require_faults_disabled,
    _reviews,
    _validate_real_diagnosis,
    _wait_for_status,
)


ALERT_NAME = "PaymentApiHigh5xxRatio"


def main() -> int:
    args = _parse_args()
    payment_token = str(
        args.payment_admin_token or ""
    ).strip()
    if len(payment_token) < 16:
        raise SystemExit(
            "PAYMENT_API_FAULT_ADMIN_TOKEN with at least "
            "16 characters is required."
        )

    agent_headers = _agent_headers(args.api_token)
    payment_headers = {
        "X-Admin-Token": payment_token,
    }
    drill_id = f"prometheus-{uuid4().hex[:12]}"
    started_at = time.monotonic()
    completed = False

    with httpx.Client(timeout=args.request_timeout) as client:
        try:
            _ready(
                client,
                f"{args.agent_url.rstrip('/')}/health",
                "OnCall Agent",
            )
            _ready(
                client,
                f"{args.payment_url.rstrip('/')}/ready",
                "payment-api",
            )
            _ready(
                client,
                f"{args.alertmanager_url.rstrip('/')}/-/ready",
                "Alertmanager",
            )
            _ready(
                client,
                f"{args.loki_url.rstrip('/')}/ready",
                "Loki",
            )

            baseline_tasks = _latest_alert_tasks(
                client,
                args.agent_url,
                agent_headers,
            )
            _reset_faults(
                client,
                args.payment_url,
                payment_headers,
            )
            _wait_for_prometheus_inactive(
                client,
                args.prometheus_url,
                timeout=args.recovery_wait_seconds,
            )
            _enable_5xx(
                client,
                args.payment_url,
                payment_headers,
            )

            group = _drive_failures_until_alert(
                client=client,
                payment_url=args.payment_url,
                prometheus_url=args.prometheus_url,
                agent_url=args.agent_url,
                payment_headers=payment_headers,
                agent_headers=agent_headers,
                baseline_tasks=baseline_tasks,
                drill_id=drill_id,
                rps=args.requests_per_second,
                timeout=args.alert_wait_seconds,
            )
            task_id = str(group["latest_task_id"])
            waiting = _wait_for_status(
                client=client,
                agent_url=args.agent_url,
                headers=agent_headers,
                task_id=task_id,
                expected={"waiting_review"},
                timeout=args.diagnosis_wait_seconds,
            )
            _validate_real_diagnosis(waiting)

            pending = [
                str(review["review_id"])
                for review in _reviews(
                    client,
                    args.agent_url,
                    agent_headers,
                    task_id,
                )
                if review["status"] == "pending"
            ]
            if not pending:
                raise RuntimeError(
                    "No pending remediation review was created."
                )

            print("Prometheus alert reached human review.", flush=True)
            print(f"- task_id: {task_id}", flush=True)
            print(
                f"- review_ids: {', '.join(pending)}",
                flush=True,
            )
            if args.auto_approve:
                _approve_reviews(
                    client=client,
                    agent_url=args.agent_url,
                    headers=agent_headers,
                    review_ids=pending,
                    reviewer=args.reviewer,
                )
            else:
                print(
                    "- approve the review in "
                    "http://127.0.0.1:8000/console",
                    flush=True,
                )

            succeeded = _wait_for_status(
                client=client,
                agent_url=args.agent_url,
                headers=agent_headers,
                task_id=task_id,
                expected={"succeeded"},
                timeout=args.approval_wait_seconds,
            )
            remediation = succeeded["result"]["metadata"].get(
                "remediation",
                {},
            )
            if remediation.get("status") != "succeeded":
                raise RuntimeError(
                    "Approved remediation did not succeed."
                )

            state_response = client.get(
                (
                    f"{args.payment_url.rstrip('/')}"
                    "/admin/fault/state"
                ),
                headers=payment_headers,
            )
            state_response.raise_for_status()
            _require_faults_disabled(state_response.json())

            _drive_healthy_traffic_until_resolved(
                client=client,
                payment_url=args.payment_url,
                prometheus_url=args.prometheus_url,
                agent_url=args.agent_url,
                agent_headers=agent_headers,
                group_id=str(group["group_id"]),
                drill_id=drill_id,
                rps=args.requests_per_second,
                timeout=args.recovery_wait_seconds,
            )
            completed = True
            elapsed = int(time.monotonic() - started_at)

            print("Full payment incident drill")
            print("- status: PASS")
            print("- trigger: Prometheus rule")
            print("- delivery: Alertmanager webhook")
            print(
                "- diagnosis: Prometheus + Loki + GitHub + "
                "Milvus + DeepSeek"
            )
            print("- approval: persisted human review")
            print(
                "- remediation: reset_payment_faults succeeded"
            )
            print("- recovery: alert group resolved")
            print(f"- task_id: {task_id}")
            print(f"- total_elapsed_seconds: {elapsed}")
            return 0
        finally:
            if not completed:
                _best_effort_reset(
                    client,
                    args.payment_url,
                    payment_headers,
                )


def _drive_failures_until_alert(
    *,
    client: httpx.Client,
    payment_url: str,
    prometheus_url: str,
    agent_url: str,
    payment_headers: dict[str, str],
    agent_headers: dict[str, str],
    baseline_tasks: set[str],
    drill_id: str,
    rps: float,
    timeout: int,
) -> dict:
    deadline = time.monotonic() + timeout
    interval = 1 / max(0.2, rps)
    next_request = 0.0
    sequence = 0
    prometheus_fired = False
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_request:
            response = _send_payment(
                client,
                payment_url,
                drill_id,
                sequence,
            )
            if response.status_code < 500:
                raise RuntimeError(
                    "Fault drill payment did not return 5xx."
                )
            sequence += 1
            next_request = now + interval

        if _prometheus_alert_state(
            client,
            prometheus_url,
        ) == "firing":
            prometheus_fired = True

        group = _new_alert_group(
            client,
            agent_url,
            agent_headers,
            baseline_tasks,
        )
        if group is not None:
            if not prometheus_fired:
                raise RuntimeError(
                    "Agent received an alert before the "
                    "Prometheus rule reached firing."
                )
            return group
        time.sleep(min(0.2, interval))
    raise TimeoutError(
        "Prometheus did not deliver a new payment alert in time."
    )


def _drive_healthy_traffic_until_resolved(
    *,
    client: httpx.Client,
    payment_url: str,
    prometheus_url: str,
    agent_url: str,
    agent_headers: dict[str, str],
    group_id: str,
    drill_id: str,
    rps: float,
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    interval = 1 / max(0.2, rps)
    next_request = 0.0
    sequence = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_request:
            response = _send_payment(
                client,
                payment_url,
                f"{drill_id}-recovery",
                sequence,
            )
            if response.status_code >= 500:
                raise RuntimeError(
                    "payment-api still returns 5xx after remediation."
                )
            sequence += 1
            next_request = now + interval

        state = _prometheus_alert_state(
            client,
            prometheus_url,
        )
        group = _alert_group(
            client,
            agent_url,
            agent_headers,
            group_id,
        )
        if state is None and group.get("status") == "resolved":
            return
        time.sleep(min(0.2, interval))
    raise TimeoutError(
        "Payment alert did not resolve after remediation."
    )


def _send_payment(
    client: httpx.Client,
    payment_url: str,
    prefix: str,
    sequence: int,
) -> httpx.Response:
    return client.post(
        f"{payment_url.rstrip('/')}/pay",
        json={
            "order_id": f"{prefix}-{sequence}",
            "user_id": "oncall-drill",
            "amount": 100,
            "currency": "CNY",
            "channel": "card",
        },
        headers={
            "X-Request-ID": f"{prefix}-{sequence}",
        },
    )


def _latest_alert_tasks(
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
) -> set[str]:
    response = client.get(
        f"{agent_url.rstrip('/')}/api/alerts/groups",
        headers=headers,
        params={"limit": 100},
    )
    response.raise_for_status()
    return {
        str(group["latest_task_id"])
        for group in response.json()
        if group.get("latest_task_id")
    }


def _new_alert_group(
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
    baseline_tasks: set[str],
) -> dict | None:
    response = client.get(
        f"{agent_url.rstrip('/')}/api/alerts/groups",
        headers=headers,
        params={"limit": 100},
    )
    response.raise_for_status()
    for group in response.json():
        if (
            group.get("labels", {}).get("alertname")
            == ALERT_NAME
            and group.get("latest_task_id")
            and str(group["latest_task_id"])
            not in baseline_tasks
        ):
            return group
    return None


def _alert_group(
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
    group_id: str,
) -> dict:
    response = client.get(
        (
            f"{agent_url.rstrip('/')}/api/alerts/groups/"
            f"{group_id}"
        ),
        headers=headers,
    )
    response.raise_for_status()
    return response.json()


def _prometheus_alert_state(
    client: httpx.Client,
    prometheus_url: str,
) -> str | None:
    response = client.get(
        f"{prometheus_url.rstrip('/')}/api/v1/alerts"
    )
    response.raise_for_status()
    payload = response.json()
    for alert in payload.get("data", {}).get("alerts", []):
        if (
            alert.get("labels", {}).get("alertname")
            == ALERT_NAME
        ):
            return str(alert.get("state") or "")
    return None


def _wait_for_prometheus_inactive(
    client: httpx.Client,
    prometheus_url: str,
    *,
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _prometheus_alert_state(
            client,
            prometheus_url,
        ) is None:
            return
        time.sleep(1)
    raise TimeoutError(
        "Existing payment alert did not become inactive."
    )


def _enable_5xx(
    client: httpx.Client,
    payment_url: str,
    headers: dict[str, str],
) -> None:
    response = client.post(
        f"{payment_url.rstrip('/')}/admin/fault/5xx",
        headers=headers,
        json={"enabled": True, "ratio": 1.0},
    )
    response.raise_for_status()


def _reset_faults(
    client: httpx.Client,
    payment_url: str,
    headers: dict[str, str],
) -> None:
    response = client.post(
        f"{payment_url.rstrip('/')}/admin/fault/reset",
        headers=headers,
    )
    response.raise_for_status()


def _best_effort_reset(
    client: httpx.Client,
    payment_url: str,
    headers: dict[str, str],
) -> None:
    try:
        _reset_faults(
            client,
            payment_url,
            headers,
        )
    except httpx.HTTPError:
        pass


def _ready(
    client: httpx.Client,
    url: str,
    service: str,
) -> None:
    response = client.get(url)
    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError(f"{service} is not ready.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full Prometheus-triggered payment incident drill."
        )
    )
    parser.add_argument(
        "--agent-url",
        default="http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--payment-url",
        default=(
            settings.payment_api_base_url
            or "http://127.0.0.1:8010"
        ),
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://127.0.0.1:9090",
    )
    parser.add_argument(
        "--alertmanager-url",
        default="http://127.0.0.1:9093",
    )
    parser.add_argument(
        "--loki-url",
        default="http://127.0.0.1:3100",
    )
    parser.add_argument(
        "--api-token",
        default=settings.api_token,
    )
    parser.add_argument(
        "--payment-admin-token",
        default=settings.payment_api_fault_admin_token,
    )
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=3,
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=30,
    )
    parser.add_argument(
        "--alert-wait-seconds",
        type=int,
        default=120,
    )
    parser.add_argument(
        "--diagnosis-wait-seconds",
        type=int,
        default=240,
    )
    parser.add_argument(
        "--approval-wait-seconds",
        type=int,
        default=600,
    )
    parser.add_argument(
        "--recovery-wait-seconds",
        type=int,
        default=180,
    )
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument(
        "--reviewer",
        default="final-acceptance",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())

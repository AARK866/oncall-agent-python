import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


TERMINAL_TASK_STATUSES = {
    "succeeded",
    "failed",
    "canceled",
    "timed_out",
}


def main() -> int:
    args = _parse_args()
    payment_token = (
        args.payment_admin_token or ""
    ).strip()
    if len(payment_token) < 16:
        raise SystemExit(
            "PAYMENT_API_FAULT_ADMIN_TOKEN with at least "
            "16 characters is required."
        )

    drill_id = f"remediation-{uuid4().hex[:12]}"
    labels = {
        "alertname": "PaymentApiHigh5xxRatio",
        "service": "payment-api",
        "severity": "critical",
        "team": "payments",
        "incident_type": "5xx",
        "drill_id": drill_id,
    }
    alert = {
        "labels": labels,
        "annotations": {
            "summary": (
                "Payment API 5xx ratio is above 20 percent"
            ),
            "description": (
                "Controlled real diagnosis and remediation drill."
            ),
            "runbook": "app/data/runbooks/payment_5xx.md",
        },
        "startsAt": datetime.now(timezone.utc).isoformat(),
        "generatorURL": (
            f"{args.prometheus_url.rstrip('/')}/alerts"
        ),
    }
    payment_headers = {
        "X-Admin-Token": payment_token,
    }
    agent_headers = _agent_headers(args.api_token)
    alertmanager_url = args.alertmanager_url.rstrip("/")
    payment_url = args.payment_url.rstrip("/")

    alert_submitted = False
    completed = False
    with httpx.Client(timeout=args.request_timeout) as client:
        try:
            _require_ready(
                client,
                f"{alertmanager_url}/-/ready",
                "Alertmanager",
            )
            _require_ready(
                client,
                f"{payment_url}/ready",
                "payment-api",
            )
            _enable_5xx(
                client,
                payment_url,
                payment_headers,
            )
            failed_requests = _generate_failures(
                client,
                payment_url,
                args.payment_requests,
                drill_id,
            )
            if failed_requests == 0:
                raise RuntimeError(
                    "payment-api did not produce any 5xx responses."
                )

            response = client.post(
                f"{alertmanager_url}/api/v2/alerts",
                json=[alert],
            )
            response.raise_for_status()
            alert_submitted = True

            group = _wait_for_group(
                client=client,
                agent_url=args.agent_url,
                headers=agent_headers,
                drill_id=drill_id,
                timeout=args.wait_seconds,
            )
            task_id = str(group["latest_task_id"])
            task = _wait_for_status(
                client=client,
                agent_url=args.agent_url,
                headers=agent_headers,
                task_id=task_id,
                expected={"waiting_review"},
                timeout=args.wait_seconds,
            )
            _validate_real_diagnosis(task)

            reviews = _reviews(
                client,
                args.agent_url,
                agent_headers,
                task_id,
            )
            review_ids = [
                str(review["review_id"])
                for review in reviews
                if review["status"] == "pending"
            ]
            if not review_ids:
                raise RuntimeError(
                    "Diagnosis did not create a pending review."
                )

            print("Real incident is waiting for approval.", flush=True)
            print(f"- drill_id: {drill_id}", flush=True)
            print(f"- task_id: {task_id}", flush=True)
            print(
                f"- review_ids: {', '.join(review_ids)}",
                flush=True,
            )
            if args.auto_approve:
                _approve_reviews(
                    client=client,
                    agent_url=args.agent_url,
                    headers=agent_headers,
                    review_ids=review_ids,
                    reviewer=args.reviewer,
                )
            else:
                print(
                    "- action: approve in the console; "
                    "this script will keep waiting.",
                    flush=True,
                )

            completed_task = _wait_for_status(
                client=client,
                agent_url=args.agent_url,
                headers=agent_headers,
                task_id=task_id,
                expected={"succeeded"},
                timeout=args.approval_wait_seconds,
            )
            remediation = completed_task["result"][
                "metadata"
            ].get("remediation", {})
            if remediation.get("status") != "succeeded":
                raise RuntimeError(
                    "Approved remediation did not succeed."
                )

            fault_state_response = client.get(
                f"{payment_url}/admin/fault/state",
                headers=payment_headers,
            )
            fault_state_response.raise_for_status()
            _require_faults_disabled(
                fault_state_response.json()
            )
            completed = True

            print("Real incident remediation acceptance")
            print("- status: PASS")
            print(f"- drill_id: {drill_id}")
            print(f"- alert_group_id: {group['group_id']}")
            print(f"- diagnosis_task_id: {task_id}")
            print(
                "- diagnosis: Prometheus + Loki + GitHub + "
                "Milvus + LLM verified"
            )
            print(
                "- remediation: approved reset_payment_faults "
                "succeeded"
            )
            return 0
        finally:
            if alert_submitted:
                _resolve_alert(
                    client,
                    alertmanager_url,
                    alert,
                )
            if not completed:
                _best_effort_reset(
                    client,
                    payment_url,
                    payment_headers,
                )


def _enable_5xx(
    client: httpx.Client,
    payment_url: str,
    headers: dict[str, str],
) -> None:
    response = client.post(
        f"{payment_url}/admin/fault/5xx",
        headers=headers,
        json={"enabled": True, "ratio": 1.0},
    )
    response.raise_for_status()


def _generate_failures(
    client: httpx.Client,
    payment_url: str,
    count: int,
    drill_id: str,
) -> int:
    failures = 0
    for index in range(max(1, count)):
        response = client.post(
            f"{payment_url}/pay",
            json={
                "order_id": f"{drill_id}-{index}",
                "user_id": "oncall-drill",
                "amount": 100,
                "currency": "CNY",
                "channel": "card",
            },
            headers={
                "X-Request-ID": f"{drill_id}-{index}",
            },
        )
        if response.status_code >= 500:
            failures += 1
    return failures


def _wait_for_group(
    *,
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
    drill_id: str,
    timeout: int,
) -> dict:
    deadline = time.monotonic() + timeout
    url = f"{agent_url.rstrip('/')}/api/alerts/groups"
    while time.monotonic() < deadline:
        response = client.get(
            url,
            headers=headers,
            params={"limit": 100},
        )
        response.raise_for_status()
        for group in response.json():
            if group.get("labels", {}).get("drill_id") == drill_id:
                return group
        time.sleep(1)
    raise TimeoutError(
        "Alertmanager delivery did not create an alert group."
    )


def _wait_for_status(
    *,
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
    task_id: str,
    expected: set[str],
    timeout: int,
) -> dict:
    deadline = time.monotonic() + timeout
    url = f"{agent_url.rstrip('/')}/api/tasks/{task_id}"
    while time.monotonic() < deadline:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        task = response.json()
        status = str(task["status"])
        if status in expected:
            return task
        if status in TERMINAL_TASK_STATUSES:
            raise RuntimeError(
                f"Diagnosis task ended with status {status}: "
                f"{task.get('error')}"
            )
        time.sleep(1)
    raise TimeoutError(
        f"Diagnosis task did not reach {sorted(expected)}."
    )


def _validate_real_diagnosis(task: dict) -> None:
    metadata = task["result"]["metadata"]
    tool_results = metadata.get("tool_results", [])
    successful = {
        result.get("tool_name"): result.get("data", {}).get(
            "provider"
        )
        for result in tool_results
        if result.get("success") is True
    }
    required = {
        "query_metrics": "prometheus",
        "query_logs": "loki",
        "query_recent_commits": "github",
    }
    missing = [
        name
        for name, provider in required.items()
        if successful.get(name) != provider
    ]
    if missing:
        raise RuntimeError(
            "Real diagnosis evidence is incomplete: "
            f"{', '.join(missing)}."
        )
    if not task["result"].get("sources"):
        raise RuntimeError(
            "Milvus knowledge retrieval returned no sources."
        )
    if metadata.get("llm_summary", {}).get("source") != "llm":
        raise RuntimeError(
            "Diagnosis summary did not come from the configured LLM."
        )
    if not metadata.get("remediation", {}).get("planned"):
        raise RuntimeError(
            "Diagnosis did not create an approved remediation plan."
        )


def _reviews(
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
    task_id: str,
) -> list[dict]:
    response = client.get(
        f"{agent_url.rstrip('/')}/api/tasks/{task_id}/reviews",
        headers=headers,
    )
    response.raise_for_status()
    return response.json()


def _approve_reviews(
    *,
    client: httpx.Client,
    agent_url: str,
    headers: dict[str, str],
    review_ids: list[str],
    reviewer: str,
) -> None:
    for review_id in review_ids:
        response = client.post(
            (
                f"{agent_url.rstrip('/')}/api/reviews/"
                f"{review_id}/approve"
            ),
            headers=headers,
            json={
                "reviewer": reviewer,
                "reason": (
                    "Approved controlled payment-api "
                    "remediation drill."
                ),
            },
        )
        response.raise_for_status()


def _resolve_alert(
    client: httpx.Client,
    alertmanager_url: str,
    alert: dict,
) -> None:
    resolved = {
        **alert,
        "endsAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.post(
            f"{alertmanager_url}/api/v2/alerts",
            json=[resolved],
        ).raise_for_status()
    except httpx.HTTPError:
        pass


def _best_effort_reset(
    client: httpx.Client,
    payment_url: str,
    headers: dict[str, str],
) -> None:
    try:
        client.post(
            f"{payment_url}/admin/fault/reset",
            headers=headers,
        )
    except httpx.HTTPError:
        pass


def _require_faults_disabled(payload: dict) -> None:
    state = payload.get("fault_state", payload)
    enabled_flags = (
        "error_5xx_enabled",
        "latency_enabled",
        "channel_failure_enabled",
    )
    if any(bool(state.get(flag)) for flag in enabled_flags):
        raise RuntimeError(
            "payment-api still has enabled faults."
        )


def _require_ready(
    client: httpx.Client,
    url: str,
    service: str,
) -> None:
    response = client.get(url)
    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError(f"{service} is not ready.")


def _agent_headers(api_token: str | None) -> dict[str, str]:
    token = str(api_token or "").strip()
    return {"X-API-Key": token} if token else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify real diagnosis, approval, and payment remediation."
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
        "--alertmanager-url",
        default="http://127.0.0.1:9093",
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://127.0.0.1:9090",
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
        "--payment-requests",
        type=int,
        default=12,
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=30,
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=180,
    )
    parser.add_argument(
        "--approval-wait-seconds",
        type=int,
        default=600,
    )
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument(
        "--reviewer",
        default="acceptance-sre",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def main() -> int:
    args = _parse_args()
    drill_id = f"alertmanager-{uuid4().hex[:12]}"
    starts_at = datetime.now(timezone.utc)
    labels = {
        "alertname": "PaymentApiWebhookDrill",
        "service": "payment-api",
        "severity": "warning",
        "team": "payments",
        "drill_id": drill_id,
    }
    alert = {
        "labels": labels,
        "annotations": {
            "summary": "Alertmanager webhook delivery drill",
            "description": (
                "Synthetic alert used to verify Alertmanager to "
                "OnCall Agent delivery."
            ),
        },
        "startsAt": starts_at.isoformat(),
        "generatorURL": f"{args.prometheus_url.rstrip('/')}/alerts",
    }

    headers = _agent_headers(args.api_token)
    with httpx.Client(timeout=args.timeout) as client:
        ready = client.get(
            f"{args.alertmanager_url.rstrip('/')}/-/ready"
        )
        ready.raise_for_status()
        submitted = client.post(
            f"{args.alertmanager_url.rstrip('/')}/api/v2/alerts",
            json=[alert],
        )
        submitted.raise_for_status()

        group = _wait_for_group(
            client=client,
            agent_url=args.agent_url,
            headers=headers,
            drill_id=drill_id,
            timeout=args.wait_seconds,
        )

        resolved_alert = {
            **alert,
            "endsAt": (
                datetime.now(timezone.utc)
                + timedelta(seconds=1)
            ).isoformat(),
        }
        resolved = client.post(
            f"{args.alertmanager_url.rstrip('/')}/api/v2/alerts",
            json=[resolved_alert],
        )
        resolved.raise_for_status()

    print("Alertmanager delivery acceptance")
    print("- status: PASS")
    print(f"- drill_id: {drill_id}")
    print(f"- alert_group_id: {group['group_id']}")
    print(f"- diagnosis_task_id: {group['latest_task_id']}")
    print("- cleanup: resolved alert submitted")
    return 0


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
        "Alertmanager did not create an OnCall Agent alert group "
        f"within {timeout} seconds."
    )


def _agent_headers(api_token: str | None) -> dict[str, str]:
    token = (api_token or "").strip()
    return {"X-API-Key": token} if token else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Alertmanager routing to the OnCall Agent."
        )
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
        "--agent-url",
        default="http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--api-token",
        default=settings.api_token,
    )
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--wait-seconds", type=int, default=30)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())

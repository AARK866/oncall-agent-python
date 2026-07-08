import argparse
import asyncio
import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


async def main() -> int:
    args = _parse_args()
    if args.webhook_secret:
        settings.webhook_secret = args.webhook_secret
    if args.in_process:
        _apply_in_process_overrides(args)

    fingerprint = args.fingerprint or f"demo-payment-5xx-{uuid4().hex}"
    payload = _sample_alertmanager_payload(args.service, args.severity, fingerprint)
    async with _api_client(args) as client:
        response = await _post_alertmanager(client, payload)
        task = await _wait_for_first_task(client, response, args.poll_attempts, args.poll_interval)
        events = await _get_first_task_events(client, response)

    summary = _summarize_response(response, task, events)
    _print_summary(summary, response, task, events, args.json)
    return 0 if _is_success(summary) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the Alertmanager webhook diagnosis flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Running API base URL.")
    parser.add_argument("--service", default="payment-api", help="Service label used in the sample alert.")
    parser.add_argument("--severity", default="critical", help="Severity label used in the sample alert.")
    parser.add_argument("--fingerprint", help="Alertmanager fingerprint. Defaults to a unique value.")
    parser.add_argument("--client-timeout", type=int, default=60, help="HTTP client timeout seconds.")
    parser.add_argument("--in-process", action="store_true", help="Call the FastAPI app in-process through ASGI.")
    parser.add_argument("--mock-llm", action="store_true", help="Use MockLLM in --in-process mode.")
    parser.add_argument("--real-tools", action="store_true", help="Use real ops tools in --in-process mode.")
    parser.add_argument("--webhook-secret", help="Set WEBHOOK_SECRET and sign the sample webhook request.")
    parser.add_argument("--poll-attempts", type=int, default=20, help="Task polling attempts.")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between task polling attempts.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def _apply_in_process_overrides(args: argparse.Namespace) -> None:
    settings.ops_tool_mode = "real" if args.real_tools else "mock"
    settings.prometheus_timeout_seconds = min(settings.prometheus_timeout_seconds, args.client_timeout)
    settings.loki_timeout_seconds = min(settings.loki_timeout_seconds, args.client_timeout)
    settings.github_timeout_seconds = min(settings.github_timeout_seconds, args.client_timeout)
    settings.embedding_timeout_seconds = min(settings.embedding_timeout_seconds, args.client_timeout)
    settings.llm_timeout_seconds = min(settings.llm_timeout_seconds, args.client_timeout)
    settings.llm_max_retries = min(settings.llm_max_retries, 1)
    if args.mock_llm:
        settings.llm_provider = "mock"
        settings.llm_api_key = None


def _api_client(args: argparse.Namespace) -> httpx.AsyncClient:
    if args.in_process:
        from app.main import app

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=args.client_timeout,
        )

    return httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=args.client_timeout)


async def _post_json(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


async def _post_alertmanager(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.webhook_secret:
        return await _post_json(client, "/api/alerts/alertmanager", payload)

    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(settings.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    response = await client.post(
        "/api/alerts/alertmanager",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-OnCall-Signature": f"sha256={signature}",
        },
    )
    response.raise_for_status()
    return response.json()


async def _get_json(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def _wait_for_first_task(
    client: httpx.AsyncClient,
    webhook_response: dict[str, Any],
    attempts: int,
    interval: float,
) -> dict[str, Any]:
    tasks = webhook_response.get("tasks") or []
    if not tasks:
        return {}

    task_id = tasks[0]["task_id"]
    task: dict[str, Any] = {}
    for _ in range(attempts):
        task = await _get_json(client, f"/api/tasks/{task_id}")
        if task.get("status") in {"succeeded", "failed"}:
            return task
        await asyncio.sleep(interval)
    return task


async def _get_first_task_events(
    client: httpx.AsyncClient,
    webhook_response: dict[str, Any],
) -> list[dict[str, Any]]:
    tasks = webhook_response.get("tasks") or []
    if not tasks:
        return []
    return await _get_json(client, f"/api/tasks/{tasks[0]['task_id']}/events")


def _sample_alertmanager_payload(service: str, severity: str, fingerprint: str) -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": f'{{}}:{{alertname="High5xxRate", service="{service}"}}',
        "status": "firing",
        "receiver": "oncall-agent",
        "commonLabels": {
            "alertname": "High5xxRate",
            "service": service,
            "severity": severity,
        },
        "commonAnnotations": {
            "summary": f"{service} has elevated 5xx responses",
            "description": "HTTP 5xx rate stayed above threshold for 5 minutes.",
        },
        "externalURL": "http://localhost:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {"instance": "demo-instance"},
                "annotations": {"runbook_url": "app/data/runbooks/payment_5xx.md"},
                "startsAt": "2026-07-06T10:00:00Z",
                "generatorURL": "http://localhost:9090/graph",
                "fingerprint": fingerprint,
            }
        ],
    }


def _summarize_response(
    response: dict[str, Any],
    task: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    first_result = task.get("result") or {}
    metadata = first_result.get("metadata", {})
    trigger = metadata.get("trigger", {})
    tool_connector = metadata.get("tool_connector", {})
    submitted_tasks = response.get("tasks", [])
    response_metadata = response.get("metadata", {})
    return {
        "received": response.get("received"),
        "processed": response.get("processed"),
        "scheduled": response_metadata.get("scheduled"),
        "deduplicated": response_metadata.get("deduplicated"),
        "alert_group_ids": response_metadata.get("alert_group_ids", []),
        "submitted_task_id": submitted_tasks[0].get("task_id") if submitted_tasks else None,
        "submitted_alert_group_id": submitted_tasks[0].get("alert_group_id") if submitted_tasks else None,
        "task_status": task.get("status"),
        "mode": first_result.get("mode"),
        "service": metadata.get("service"),
        "trigger_source": trigger.get("source"),
        "alert_id": trigger.get("alert_id"),
        "connector_name": tool_connector.get("connector_name"),
        "connector_mode": tool_connector.get("mode"),
        "runbook_retrieved_count": metadata.get("runbook_retrieved_count", 0),
        "incident_id": metadata.get("incident_id"),
        "answer_present": bool(first_result.get("answer")),
        "event_count": len(events),
        "event_types": [event.get("event_type") for event in events],
    }


def _is_success(summary: dict[str, Any]) -> bool:
    return (
        summary["received"] == 1
        and summary["processed"] == 1
        and summary["task_status"] == "succeeded"
        and summary["mode"] == "ops"
        and summary["trigger_source"] == "alertmanager"
        and summary["answer_present"] is True
        and "succeeded" in summary["event_types"]
    )


def _print_summary(
    summary: dict[str, Any],
    response: dict[str, Any],
    task: dict[str, Any],
    events: list[dict[str, Any]],
    as_json: bool,
) -> None:
    if as_json:
        print(
            json.dumps(
                {"summary": summary, "response": response, "task": task, "events": events},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print("Alert webhook check")
    print(f"- received: {summary['received']}")
    print(f"- processed: {summary['processed']}")
    print(f"- scheduled: {summary['scheduled']}")
    print(f"- deduplicated: {summary['deduplicated']}")
    print(f"- alert_group_ids: {', '.join(str(item) for item in summary['alert_group_ids'])}")
    print(f"- task_id: {summary['submitted_task_id']}")
    print(f"- task_alert_group_id: {summary['submitted_alert_group_id']}")
    print(f"- task_status: {summary['task_status']}")
    print(f"- mode: {summary['mode']}")
    print(f"- service: {summary['service']}")
    print(f"- trigger: {summary['trigger_source']} alert_id={summary['alert_id']}")
    print(f"- connector: {summary['connector_name']} ({summary['connector_mode']})")
    print(f"- runbook_retrieved_count: {summary['runbook_retrieved_count']}")
    print(f"- incident_id: {summary['incident_id']}")
    print(f"- event_count: {summary['event_count']}")
    print(f"- event_types: {', '.join(str(item) for item in summary['event_types'])}")
    print("")
    print("Answer preview")
    result = task.get("result") or {}
    if result:
        print(str(result.get("answer", ""))[:1200])


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

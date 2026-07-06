import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


async def main() -> int:
    args = _parse_args()
    if args.in_process:
        _apply_in_process_overrides(args)

    payload = _sample_alertmanager_payload(args.service, args.severity)
    async with _api_client(args) as client:
        response = await _post_json(client, "/api/alerts/alertmanager", payload)

    summary = _summarize_response(response)
    _print_summary(summary, response, args.json)
    return 0 if _is_success(summary) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the Alertmanager webhook diagnosis flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Running API base URL.")
    parser.add_argument("--service", default="payment-api", help="Service label used in the sample alert.")
    parser.add_argument("--severity", default="critical", help="Severity label used in the sample alert.")
    parser.add_argument("--client-timeout", type=int, default=60, help="HTTP client timeout seconds.")
    parser.add_argument("--in-process", action="store_true", help="Call the FastAPI app in-process through ASGI.")
    parser.add_argument("--mock-llm", action="store_true", help="Use MockLLM in --in-process mode.")
    parser.add_argument("--real-tools", action="store_true", help="Use real ops tools in --in-process mode.")
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


def _sample_alertmanager_payload(service: str, severity: str) -> dict[str, Any]:
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
                "fingerprint": "demo-payment-5xx",
            }
        ],
    }


def _summarize_response(response: dict[str, Any]) -> dict[str, Any]:
    results = response.get("results", [])
    first_result = results[0] if results else {}
    metadata = first_result.get("metadata", {})
    trigger = metadata.get("trigger", {})
    tool_connector = metadata.get("tool_connector", {})
    return {
        "received": response.get("received"),
        "processed": response.get("processed"),
        "mode": first_result.get("mode"),
        "service": metadata.get("service"),
        "trigger_source": trigger.get("source"),
        "alert_id": trigger.get("alert_id"),
        "connector_name": tool_connector.get("connector_name"),
        "connector_mode": tool_connector.get("mode"),
        "runbook_retrieved_count": metadata.get("runbook_retrieved_count", 0),
        "incident_id": metadata.get("incident_id"),
        "answer_present": bool(first_result.get("answer")),
    }


def _is_success(summary: dict[str, Any]) -> bool:
    return (
        summary["received"] == 1
        and summary["processed"] == 1
        and summary["mode"] == "ops"
        and summary["trigger_source"] == "alertmanager"
        and summary["answer_present"] is True
    )


def _print_summary(summary: dict[str, Any], response: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"summary": summary, "response": response}, ensure_ascii=False, indent=2))
        return

    print("Alert webhook check")
    print(f"- received: {summary['received']}")
    print(f"- processed: {summary['processed']}")
    print(f"- mode: {summary['mode']}")
    print(f"- service: {summary['service']}")
    print(f"- trigger: {summary['trigger_source']} alert_id={summary['alert_id']}")
    print(f"- connector: {summary['connector_name']} ({summary['connector_mode']})")
    print(f"- runbook_retrieved_count: {summary['runbook_retrieved_count']}")
    print(f"- incident_id: {summary['incident_id']}")
    print("")
    print("Answer preview")
    results = response.get("results", [])
    if results:
        print(str(results[0].get("answer", ""))[:1200])


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

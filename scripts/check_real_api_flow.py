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


DEFAULT_QUESTION = "payment service 5xx error rate is high after recent code changes"


async def main() -> int:
    args = _parse_args()
    if args.in_process:
        _apply_in_process_overrides(args)

    async with _api_client(args) as client:
        health = await _get_json(client, "/api/tools/health?mode=real")
        response = await _post_json(
            client,
            "/api/incidents/analyze",
            {
                "message": args.question,
                "session_id": args.session_id,
                "mode": "ops",
            },
        )

    summary = _summarize_api_response(health, response)
    _print_summary(summary, response, args.json)
    return 0 if _is_success(summary, args.allow_tool_failures) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the real /api/incidents/analyze HTTP flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Running API base URL.")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="Incident question to diagnose.")
    parser.add_argument("--session-id", default="real-api-check", help="Session id for the diagnosis.")
    parser.add_argument("--client-timeout", type=int, default=60, help="HTTP client timeout seconds.")
    parser.add_argument("--in-process", action="store_true", help="Call the FastAPI app in-process through ASGI.")
    parser.add_argument("--mock-llm", action="store_true", help="Use MockLLM in --in-process mode.")
    parser.add_argument("--allow-tool-failures", action="store_true", help="Exit 0 even if required tools fail.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def _apply_in_process_overrides(args: argparse.Namespace) -> None:
    settings.ops_tool_mode = "real"
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


async def _get_json(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def _post_json(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def _summarize_api_response(health: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    metadata = response.get("metadata", {})
    tool_connector = metadata.get("tool_connector", {})
    tool_statuses = _tool_statuses(metadata.get("tool_results", []))
    required_tools = _required_tools()
    failed_required_tools = [
        tool_name
        for tool_name in required_tools
        if not tool_statuses.get(tool_name, {}).get("success", False)
    ]
    return {
        "health_ready": health.get("ready"),
        "health_tools": health.get("tools", []),
        "mode": response.get("mode"),
        "answer_present": bool(response.get("answer")),
        "service": metadata.get("service"),
        "connector_mode": tool_connector.get("mode"),
        "connector_name": tool_connector.get("connector_name"),
        "runbook_retrieved_count": metadata.get("runbook_retrieved_count", 0),
        "incident_id": metadata.get("incident_id"),
        "diagnosis_id": metadata.get("diagnosis_id"),
        "required_tools": required_tools,
        "failed_required_tools": failed_required_tools,
        "tool_statuses": tool_statuses,
    }


def _required_tools() -> list[str]:
    return ["query_metrics", "query_logs", "query_recent_commits"]


def _tool_statuses(raw_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for result in raw_results:
        data = result.get("data") or {}
        statuses[result.get("tool_name", "unknown")] = {
            "success": bool(result.get("success")),
            "provider": data.get("provider"),
            "error": result.get("error"),
            "elapsed_ms": result.get("elapsed_ms"),
            "summary": data.get("summary"),
        }
    return statuses


def _is_success(summary: dict[str, Any], allow_tool_failures: bool) -> bool:
    if summary["mode"] != "ops":
        return False
    if summary["connector_mode"] != "real":
        return False
    if summary["runbook_retrieved_count"] <= 0:
        return False
    if not summary["answer_present"]:
        return False
    if allow_tool_failures:
        return True
    return not summary["failed_required_tools"]


def _print_summary(summary: dict[str, Any], response: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"summary": summary, "response": response}, ensure_ascii=False, indent=2))
        return

    print("Real API flow check")
    print(f"- mode: {summary['mode']}")
    print(f"- service: {summary['service']}")
    print(f"- connector: {summary['connector_name']} ({summary['connector_mode']})")
    print(f"- runbook_retrieved_count: {summary['runbook_retrieved_count']}")
    print(f"- incident_id: {summary['incident_id']}")
    print(f"- diagnosis_id: {summary['diagnosis_id']}")
    print("")
    print("Tools")
    for tool_name, status in summary["tool_statuses"].items():
        marker = "PASS" if status["success"] else "FAIL"
        provider = status.get("provider") or "-"
        detail = status.get("summary") or status.get("error") or ""
        print(f"- [{marker}] {tool_name} provider={provider} {detail}")
    print("")
    if summary["connector_mode"] != "real":
        print("API is not using real tools. Set OPS_TOOL_MODE=real and restart the API server.")
    elif summary["failed_required_tools"]:
        print(f"Failed required tools: {', '.join(summary['failed_required_tools'])}")
    else:
        print("All required API tools passed.")
    print("")
    print("Answer preview")
    print(str(response.get("answer", ""))[:1200])


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

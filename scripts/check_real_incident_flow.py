import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents import KnowledgeAgent, OpsAgent
from app.config import settings
from app.llm import MockLLMClient, create_llm_client
from app.storage import SQLiteIncidentStore
from app.tools import create_ops_tool_registry


DEFAULT_QUESTION = "payment service 5xx error rate is high after recent code changes"


async def main() -> int:
    args = _parse_args()
    _apply_runtime_settings(args)

    llm = MockLLMClient(default_answer="Mock LLM answer for real incident flow check.") if args.mock_llm else create_llm_client()
    _tune_client_for_health_check(llm, args.client_timeout)

    registry = create_ops_tool_registry(mode="real")
    agent = OpsAgent(
        tool_registry=registry,
        knowledge_agent=KnowledgeAgent.from_runbook_directory(llm=llm),
        incident_store=None if args.no_persist else SQLiteIncidentStore.from_settings(),
        llm=llm,
    )

    response = await agent.analyze(
        question=args.question,
        session_id=args.session_id,
        service=args.service,
    )

    summary = _summarize_response(response.metadata, args.question)
    _print_summary(summary, response.answer, args.json)
    return 0 if _is_success(summary, args.allow_tool_failures) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real OpsAgent incident diagnosis flow.")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="Incident question to diagnose.")
    parser.add_argument("--session-id", default="real-incident-check", help="Session id for the diagnosis.")
    parser.add_argument("--service", default="payment-api", help="Service name to diagnose.")
    parser.add_argument("--mock-llm", action="store_true", help="Use MockLLM while keeping real ops tools and RAG.")
    parser.add_argument("--no-persist", action="store_true", help="Do not persist the diagnosis to SQLite.")
    parser.add_argument("--allow-tool-failures", action="store_true", help="Exit 0 even if required tools fail.")
    parser.add_argument("--client-timeout", type=int, default=30, help="Timeout seconds for real clients.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def _apply_runtime_settings(args: argparse.Namespace) -> None:
    settings.ops_tool_mode = "real"
    settings.prometheus_timeout_seconds = min(settings.prometheus_timeout_seconds, args.client_timeout)
    settings.loki_timeout_seconds = min(settings.loki_timeout_seconds, args.client_timeout)
    settings.gitlab_timeout_seconds = min(settings.gitlab_timeout_seconds, args.client_timeout)
    settings.github_timeout_seconds = min(settings.github_timeout_seconds, args.client_timeout)
    settings.embedding_timeout_seconds = min(settings.embedding_timeout_seconds, args.client_timeout)
    settings.llm_timeout_seconds = min(settings.llm_timeout_seconds, args.client_timeout)
    settings.llm_max_retries = min(settings.llm_max_retries, 1)


def _summarize_response(metadata: dict[str, Any], question: str) -> dict[str, Any]:
    tool_statuses = _tool_statuses(metadata.get("tool_results", []))
    required_tools = _required_tools()
    failed_required_tools = [
        tool_name
        for tool_name in required_tools
        if not tool_statuses.get(tool_name, {}).get("success", False)
    ]
    return {
        "question": question,
        "service": metadata.get("service"),
        "tool_connector": metadata.get("tool_connector", {}),
        "graph_runtime": metadata.get("graph_runtime", {}),
        "runbook_retrieved_count": metadata.get("runbook_retrieved_count", 0),
        "required_tools": required_tools,
        "failed_required_tools": failed_required_tools,
        "tool_statuses": tool_statuses,
        "incident_id": metadata.get("incident_id"),
        "diagnosis_id": metadata.get("diagnosis_id"),
    }


def _required_tools() -> list[str]:
    tools = ["query_metrics", "query_logs", "query_recent_commits"]
    if settings.gitlab_base_url and settings.gitlab_project_id:
        tools.append("query_deployments")
    return tools


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
    if summary["runbook_retrieved_count"] <= 0:
        return False
    if allow_tool_failures:
        return True
    return not summary["failed_required_tools"]


def _print_summary(summary: dict[str, Any], answer: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"summary": summary, "answer": answer}, ensure_ascii=False, indent=2))
        return

    print("Real incident flow check")
    print(f"- question: {summary['question']}")
    print(f"- service: {summary['service']}")
    print(f"- connector: {summary['tool_connector'].get('connector_name')} ({summary['tool_connector'].get('mode')})")
    print(f"- graph_runtime: {summary['graph_runtime'].get('used')}")
    print(f"- runbook_retrieved_count: {summary['runbook_retrieved_count']}")
    print(f"- incident_id: {summary.get('incident_id')}")
    print("")
    print("Tools")
    for tool_name, status in summary["tool_statuses"].items():
        marker = "PASS" if status["success"] else "FAIL"
        provider = status.get("provider") or "-"
        detail = status.get("summary") or status.get("error") or ""
        print(f"- [{marker}] {tool_name} provider={provider} {detail}")
    print("")
    if summary["failed_required_tools"]:
        print(f"Failed required tools: {', '.join(summary['failed_required_tools'])}")
    else:
        print("All required tools passed.")
    print("")
    print("Answer preview")
    print(answer[:1200])


def _tune_client_for_health_check(client: object, timeout_seconds: int) -> None:
    if hasattr(client, "timeout_seconds"):
        setattr(client, "timeout_seconds", timeout_seconds)
    if hasattr(client, "max_retries"):
        setattr(client, "max_retries", min(int(getattr(client, "max_retries")), 1))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

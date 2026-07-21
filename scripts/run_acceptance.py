import argparse
import asyncio
import hashlib
import hmac
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.rag import KnowledgeIngestionPipeline


@dataclass(frozen=True)
class AcceptanceCheck:
    name: str
    status: str
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)


async def main() -> int:
    args = _parse_args()
    _apply_runtime_overrides(args)

    checks: list[AcceptanceCheck] = []
    async with _api_client(args.client_timeout) as client:
        checks.append(await _check_health(client))
        checks.append(await _check_tools(client))
        if not args.skip_ingest:
            checks.append(await _check_knowledge_ingest(args))
        checks.append(await _check_knowledge_search(client))
        alert_check, task_id, incident_id, group_id = await _check_alert_flow(client, args)
        checks.append(alert_check)
        checks.append(await _check_task_events(client, task_id))
        checks.append(await _check_alert_group(client, group_id))
        checks.append(await _check_incident_history(client, incident_id))

    _print_summary(checks, args.json)
    return 0 if all(check.status == "PASS" for check in checks) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an end-to-end OnCall Agent acceptance check.")
    parser.add_argument("--client-timeout", type=int, default=60, help="HTTP client timeout seconds.")
    parser.add_argument("--real-env", action="store_true", help="Use current .env integrations instead of local mock overrides.")
    parser.add_argument("--real-tools", action="store_true", help="Use real ops tools while keeping other local-safe overrides.")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip knowledge ingestion step.")
    parser.add_argument("--webhook-secret", default="acceptance-secret", help="Temporary webhook secret used for signed checks.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def _apply_runtime_overrides(args: argparse.Namespace) -> None:
    settings.llm_timeout_seconds = min(settings.llm_timeout_seconds, args.client_timeout)
    settings.llm_max_retries = min(settings.llm_max_retries, 1)
    settings.embedding_timeout_seconds = min(settings.embedding_timeout_seconds, args.client_timeout)
    settings.embedding_max_retries = min(settings.embedding_max_retries, 1)
    settings.prometheus_timeout_seconds = min(settings.prometheus_timeout_seconds, args.client_timeout)
    settings.loki_timeout_seconds = min(settings.loki_timeout_seconds, args.client_timeout)
    settings.github_timeout_seconds = min(settings.github_timeout_seconds, args.client_timeout)

    if args.real_env:
        if args.webhook_secret:
            settings.webhook_secret = args.webhook_secret
        return

    settings.app_env = "local"
    settings.api_auth_enabled = False
    settings.api_token = None
    settings.webhook_secret = args.webhook_secret
    settings.llm_provider = "mock"
    settings.llm_api_key = None
    settings.embedding_provider = "hash"
    settings.embedding_api_key = None
    settings.embedding_dimensions = 128
    settings.knowledge_vector_store = "in_memory"
    settings.knowledge_retriever_mode = "hybrid"
    settings.knowledge_local_path = "app/data/runbooks"
    settings.ops_tool_mode = "real" if args.real_tools else "mock"


def _api_client(timeout_seconds: int) -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=timeout_seconds,
    )


async def _check_health(client: httpx.AsyncClient) -> AcceptanceCheck:
    data = await _get_json(client, "/health")
    if data.get("status") != "ok":
        return AcceptanceCheck("health", "FAIL", f"unexpected health payload: {data}")
    return AcceptanceCheck("health", "PASS", f"{data.get('app')} {data.get('version')} is healthy")


async def _check_tools(client: httpx.AsyncClient) -> AcceptanceCheck:
    data = await _get_json(client, "/api/tools/health")
    if not data.get("ready"):
        return AcceptanceCheck("tools", "FAIL", str(data.get("message") or data))
    return AcceptanceCheck(
        "tools",
        "PASS",
        f"{data.get('connector_name')} ready with {len(data.get('tools', []))} tools",
        metadata={"connector": data.get("connector_name"), "mode": data.get("mode")},
    )


async def _check_knowledge_ingest(args: argparse.Namespace) -> AcceptanceCheck:
    result = await KnowledgeIngestionPipeline().ingest(
        source="local",
        path=settings.knowledge_local_path,
        chunk_size=settings.knowledge_ingest_chunk_size,
        chunk_overlap=settings.knowledge_ingest_chunk_overlap,
    )
    if result.documents_loaded <= 0 or result.chunks_created <= 0:
        return AcceptanceCheck("knowledge_ingest", "FAIL", result.model_dump_json())
    return AcceptanceCheck(
        "knowledge_ingest",
        "PASS",
        f"loaded {result.documents_loaded} document(s), created {result.chunks_created} chunk(s)",
        metadata=result.model_dump(mode="json"),
    )


async def _check_knowledge_search(client: httpx.AsyncClient) -> AcceptanceCheck:
    data = await _post_json(
        client,
        "/api/knowledge/search",
        {
            "query": "payment service 5xx database connection pool",
            "top_k": 2,
            "service": "payment-api",
            "incident_type": "5xx",
        },
    )
    retrieved_count = int(data.get("metadata", {}).get("retrieved_count") or 0)
    if retrieved_count <= 0:
        return AcceptanceCheck("knowledge_search", "FAIL", "no runbook chunks retrieved")
    return AcceptanceCheck("knowledge_search", "PASS", f"retrieved {retrieved_count} runbook chunk(s)")


async def _check_alert_flow(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
) -> tuple[AcceptanceCheck, str, str, str]:
    fingerprint = f"acceptance-{uuid4().hex}"
    payload = _alertmanager_payload(fingerprint=fingerprint)
    webhook_response = await _post_signed_alertmanager(client, payload)
    tasks = webhook_response.get("tasks") or []
    if not tasks:
        return (
            AcceptanceCheck("alert_flow", "FAIL", "webhook created no task"),
            "",
            "",
            "",
        )

    task = await _wait_for_task(client, tasks[0]["task_id"])
    approved_reviews = 0
    if task.get("status") == "waiting_review":
        approved_reviews = await _approve_pending_reviews(client, str(task["task_id"]))
        task = await _wait_for_task(client, str(task["task_id"]))

    result = task.get("result") or {}
    metadata = result.get("metadata", {})
    incident_id = str(metadata.get("incident_id") or "")
    group_id = str(task.get("alert_group_id") or "")
    if task.get("status") != "succeeded" or not incident_id or not group_id:
        return (
            AcceptanceCheck("alert_flow", "FAIL", f"task did not complete correctly: {task}"),
            str(task.get("task_id") or ""),
            incident_id,
            group_id,
        )

    return (
        AcceptanceCheck(
            "alert_flow",
            "PASS",
            f"task {task['task_id']} completed for incident {incident_id}",
            metadata={
                "task_id": task["task_id"],
                "incident_id": incident_id,
                "alert_group_id": group_id,
                "approved_reviews": approved_reviews,
                "connector": metadata.get("tool_connector", {}),
            },
        ),
        str(task["task_id"]),
        incident_id,
        group_id,
    )


async def _check_task_events(client: httpx.AsyncClient, task_id: str) -> AcceptanceCheck:
    if not task_id:
        return AcceptanceCheck("task_events", "FAIL", "missing task id")

    events = await _get_json(client, f"/api/tasks/{task_id}/events")
    event_types = [event.get("event_type") for event in events]
    required_events = {
        "queued",
        "running",
        "waiting_review",
        "human_review_approved",
        "retrieved_docs",
        "incident_persisted",
        "succeeded",
    }
    missing = sorted(required_events - set(event_types))
    if missing:
        return AcceptanceCheck("task_events", "FAIL", f"missing events: {', '.join(missing)}")
    return AcceptanceCheck("task_events", "PASS", f"recorded {len(events)} progress event(s)")


async def _check_alert_group(client: httpx.AsyncClient, group_id: str) -> AcceptanceCheck:
    if not group_id:
        return AcceptanceCheck("alert_group", "FAIL", "missing alert group id")

    group = await _get_json(client, f"/api/alerts/groups/{group_id}")
    if group.get("status") != "active":
        return AcceptanceCheck("alert_group", "FAIL", f"unexpected group status: {group.get('status')}")
    return AcceptanceCheck("alert_group", "PASS", f"group {group_id} is active")


async def _check_incident_history(client: httpx.AsyncClient, incident_id: str) -> AcceptanceCheck:
    if not incident_id:
        return AcceptanceCheck("incident_history", "FAIL", "missing incident id")

    detail = await _get_json(client, f"/api/incidents/{incident_id}")
    latest_diagnosis = detail.get("latest_diagnosis")
    if not latest_diagnosis:
        return AcceptanceCheck("incident_history", "FAIL", "incident has no diagnosis")
    return AcceptanceCheck("incident_history", "PASS", f"incident {incident_id} has latest diagnosis")


async def _get_json(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def _post_json(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


async def _approve_pending_reviews(client: httpx.AsyncClient, task_id: str) -> int:
    reviews = await _get_json(client, f"/api/tasks/{task_id}/reviews")
    approved_count = 0
    for review in reviews:
        if review.get("status") != "pending":
            continue
        await _post_json(
            client,
            f"/api/reviews/{review['review_id']}/approve",
            {
                "reviewer": "acceptance",
                "reason": "Acceptance check approves high-risk action gate.",
            },
        )
        approved_count += 1
    return approved_count


async def _post_signed_alertmanager(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
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


async def _wait_for_task(
    client: httpx.AsyncClient,
    task_id: str,
    attempts: int = 20,
    interval: float = 1.0,
) -> dict[str, Any]:
    task: dict[str, Any] = {}
    for _ in range(attempts):
        task = await _get_json(client, f"/api/tasks/{task_id}")
        if task.get("status") in {"succeeded", "failed", "waiting_review"}:
            return task
        await asyncio.sleep(interval)
    return task


def _alertmanager_payload(fingerprint: str) -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="AcceptanceHigh5xxRate", service="payment-api"}',
        "status": "firing",
        "receiver": "oncall-agent",
        "commonLabels": {
            "alertname": "AcceptanceHigh5xxRate",
            "service": "payment-api",
            "severity": "critical",
        },
        "commonAnnotations": {
            "summary": "payment-api acceptance alert",
            "description": "Acceptance check for payment-api 5xx diagnosis.",
        },
        "alerts": [
            {
                "status": "firing",
                "labels": {"instance": "acceptance"},
                "startsAt": "2026-07-08T10:00:00Z",
                "fingerprint": fingerprint,
            }
        ],
    }


def _print_summary(checks: list[AcceptanceCheck], as_json: bool) -> None:
    if as_json:
        print(json.dumps([check.__dict__ for check in checks], ensure_ascii=False, indent=2))
        return

    print("OnCall Agent acceptance check")
    for check in checks:
        print(f"- [{check.status}] {check.name}: {check.detail}")
    passed = sum(1 for check in checks if check.status == "PASS")
    failed = len(checks) - passed
    print("")
    print(f"Summary: {passed} passed, {failed} failed")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

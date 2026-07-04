import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.llm import create_llm_client
from app.rag import KnowledgeBase, create_embedding_model
from app.schemas import ChatMessage, MessageRole
from app.tools import GitHubClient

ACTIVE_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


async def main() -> int:
    global ACTIVE_TIMEOUT_SECONDS
    args = _parse_args()
    ACTIVE_TIMEOUT_SECONDS = args.client_timeout
    _tune_settings_for_health_check()
    checks: list[tuple[str, Callable[[], Awaitable[CheckResult]]]] = [
        ("config", _check_config),
    ]

    if not args.config_only:
        if not args.skip_llm:
            checks.append(("llm", _check_llm))
        if not args.skip_embedding:
            checks.append(("embedding", _check_embedding))
        if not args.skip_milvus:
            checks.append(("milvus", _check_milvus))
        if not args.skip_rag:
            checks.append(("rag", _check_rag))
        if not args.skip_prometheus:
            checks.append(("prometheus", _check_prometheus))
        if not args.skip_loki:
            checks.append(("loki", _check_loki))
        if not args.skip_github:
            checks.append(("github", _check_github))

    print("Enterprise stack check", flush=True)
    print(f"- app: {settings.app_name}", flush=True)
    print(f"- env: {settings.app_env}", flush=True)
    print(f"- llm_provider: {settings.llm_provider}", flush=True)
    print(f"- embedding_provider: {settings.embedding_provider}", flush=True)
    print(f"- vector_store: {settings.knowledge_vector_store}", flush=True)
    print("", flush=True)

    results: list[CheckResult] = []
    results_by_name: dict[str, CheckResult] = {}
    for name, check in checks:
        dependency_skip = _dependency_skip(name, results_by_name)
        if dependency_skip:
            results.append(dependency_skip)
            results_by_name[name] = dependency_skip
            print(f"[{dependency_skip.status}] {dependency_skip.name}: {dependency_skip.detail}", flush=True)
            continue

        print(f"[RUN] {name}", flush=True)
        result = await _safe_check(name, check)
        results.append(result)
        results_by_name[name] = result
        print(f"[{result.status}] {result.name}: {result.detail}", flush=True)

    passed = sum(1 for result in results if result.status == "PASS")
    skipped = sum(1 for result in results if result.status == "SKIP")
    failed = sum(1 for result in results if result.status == "FAIL")

    print("", flush=True)
    print(f"Summary: {passed} passed, {skipped} skipped, {failed} failed", flush=True)
    return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check real enterprise integrations.")
    parser.add_argument("--config-only", action="store_true", help="Only validate local .env configuration.")
    parser.add_argument("--skip-llm", action="store_true", help="Do not call the real LLM provider.")
    parser.add_argument("--skip-embedding", action="store_true", help="Do not call the embedding provider.")
    parser.add_argument("--skip-milvus", action="store_true", help="Do not connect to Milvus.")
    parser.add_argument("--skip-rag", action="store_true", help="Do not run the RAG retrieval check.")
    parser.add_argument("--skip-prometheus", action="store_true", help="Do not query Prometheus.")
    parser.add_argument("--skip-loki", action="store_true", help="Do not query Loki.")
    parser.add_argument("--skip-github", action="store_true", help="Do not query GitHub.")
    parser.add_argument("--client-timeout", type=int, default=10, help="Timeout seconds used by active checks.")
    return parser.parse_args()


async def _safe_check(
    name: str,
    check: Callable[[], Awaitable[CheckResult]],
) -> CheckResult:
    try:
        return await check()
    except Exception as exc:
        return CheckResult(name=name, status="FAIL", detail=_redact(str(exc) or exc.__class__.__name__))


async def _check_config() -> CheckResult:
    missing: list[str] = []

    if settings.llm_provider.lower().strip() not in {"mock", "local"} and not settings.llm_api_key:
        missing.append("LLM_API_KEY")

    embedding_provider = settings.embedding_provider.lower().strip()
    if embedding_provider not in {"hash", "local", "mock"} and not settings.embedding_api_key:
        missing.append("EMBEDDING_API_KEY")

    if settings.knowledge_vector_store.lower().strip() == "milvus" and not settings.milvus_uri:
        missing.append("MILVUS_URI")

    if settings.github_repo and "/" not in settings.github_repo:
        missing.append("GITHUB_REPO owner/name format")

    if missing:
        return CheckResult("config", "FAIL", f"missing or invalid: {', '.join(missing)}")

    return CheckResult(
        "config",
        "PASS",
        "required real LLM, embedding, vector store, and optional GitHub settings are readable",
    )


async def _check_llm() -> CheckResult:
    client = create_llm_client()
    _tune_client_for_health_check(client)
    messages = [
        ChatMessage(role=MessageRole.system, content="You are a concise enterprise stack checker."),
        ChatMessage(role=MessageRole.user, content="Reply with one short sentence."),
    ]
    answer = await client.generate(messages)
    if not answer.strip():
        return CheckResult("llm", "FAIL", "provider returned an empty response")
    return CheckResult("llm", "PASS", f"{settings.llm_model} returned: {answer.strip()[:120]}")


async def _check_embedding() -> CheckResult:
    model = create_embedding_model()
    _tune_client_for_health_check(model)
    vector = await asyncio.to_thread(model.embed, "payment service 5xx error rate is high")
    if not vector:
        return CheckResult("embedding", "FAIL", "provider returned an empty vector")
    if len(vector) != settings.embedding_dimensions:
        return CheckResult(
            "embedding",
            "FAIL",
            f"expected {settings.embedding_dimensions} dimensions, got {len(vector)}",
        )
    return CheckResult("embedding", "PASS", f"{settings.embedding_model} returned {len(vector)} dimensions")


async def _check_milvus() -> CheckResult:
    if settings.knowledge_vector_store.lower().strip() != "milvus":
        return CheckResult("milvus", "SKIP", "KNOWLEDGE_VECTOR_STORE is not milvus")
    if not settings.milvus_uri:
        return CheckResult("milvus", "FAIL", "MILVUS_URI is empty")

    collections = await asyncio.to_thread(_list_milvus_collections)
    return CheckResult(
        "milvus",
        "PASS",
        f"connected to {settings.milvus_uri}; collections={collections}",
    )


def _list_milvus_collections() -> list[str]:
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise RuntimeError("pymilvus is not installed; run pip install -r requirements.txt") from exc

    kwargs = {"uri": settings.milvus_uri}
    if settings.milvus_token:
        kwargs["token"] = settings.milvus_token
    if settings.milvus_db_name:
        kwargs["db_name"] = settings.milvus_db_name

    client = MilvusClient(**kwargs)
    return client.list_collections()


async def _check_rag() -> CheckResult:
    if settings.knowledge_vector_store.lower().strip() != "milvus":
        return CheckResult("rag", "SKIP", "KNOWLEDGE_VECTOR_STORE is not milvus")

    def search() -> int:
        knowledge_base = KnowledgeBase.from_directory("app/data/runbooks")
        results = knowledge_base.search("payment service 5xx database connection pool", top_k=2)
        return len(results)

    count = await asyncio.to_thread(search)
    if count <= 0:
        return CheckResult("rag", "FAIL", "Milvus search returned no runbook chunks")
    return CheckResult("rag", "PASS", f"retrieved {count} runbook chunk(s) from Milvus")


async def _check_prometheus() -> CheckResult:
    if not settings.prometheus_base_url:
        return CheckResult("prometheus", "SKIP", "PROMETHEUS_BASE_URL is empty")

    base_url = settings.prometheus_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_active_timeout(settings.prometheus_timeout_seconds)) as client:
        response = await client.get(f"{base_url}/api/v1/query", params={"query": "up"})
        response.raise_for_status()
        data = response.json()

    status = data.get("status")
    if status != "success":
        return CheckResult("prometheus", "FAIL", f"unexpected status: {status}")
    return CheckResult("prometheus", "PASS", f"{base_url} query API is reachable")


async def _check_loki() -> CheckResult:
    if not settings.loki_base_url:
        return CheckResult("loki", "SKIP", "LOKI_BASE_URL is empty")

    base_url = settings.loki_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_active_timeout(settings.loki_timeout_seconds)) as client:
        response = await client.get(f"{base_url}/ready")
        response.raise_for_status()

    return CheckResult("loki", "PASS", f"{base_url} readiness endpoint is reachable")


async def _check_github() -> CheckResult:
    if not settings.github_repo:
        return CheckResult("github", "SKIP", "GITHUB_REPO is empty")
    if not settings.github_token:
        return CheckResult("github", "SKIP", "GITHUB_TOKEN is empty")

    client = GitHubClient(timeout_seconds=int(_active_timeout(settings.github_timeout_seconds)))
    data = await client.list_commits(limit=1)
    commits = data.get("commits", [])
    if not commits:
        return CheckResult("github", "FAIL", "GitHub returned no commits")
    sha = str(commits[0].get("sha", ""))[:12]
    return CheckResult("github", "PASS", f"{settings.github_repo}@{settings.github_branch} latest sha={sha}")


def _redact(text: str) -> str:
    redacted = text
    for secret in [
        settings.llm_api_key,
        settings.embedding_api_key,
        settings.github_token,
        settings.gitlab_token,
        settings.milvus_token,
    ]:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def _dependency_skip(name: str, results_by_name: dict[str, CheckResult]) -> CheckResult | None:
    if name != "rag":
        return None

    failed_dependencies = [
        dependency
        for dependency in ["embedding", "milvus"]
        if results_by_name.get(dependency)
        and results_by_name[dependency].status == "FAIL"
    ]
    if failed_dependencies:
        return CheckResult(
            "rag",
            "SKIP",
            f"skipped because {', '.join(failed_dependencies)} failed",
        )
    return None


def _tune_settings_for_health_check() -> None:
    settings.llm_timeout_seconds = int(_active_timeout(settings.llm_timeout_seconds))
    settings.llm_max_retries = min(settings.llm_max_retries, 1)
    settings.embedding_timeout_seconds = int(_active_timeout(settings.embedding_timeout_seconds))
    settings.embedding_max_retries = min(settings.embedding_max_retries, 1)


def _tune_client_for_health_check(client: object) -> None:
    if hasattr(client, "timeout_seconds"):
        setattr(client, "timeout_seconds", _active_timeout(getattr(client, "timeout_seconds")))
    if hasattr(client, "max_retries"):
        setattr(client, "max_retries", min(int(getattr(client, "max_retries")), 1))


def _active_timeout(configured_timeout: int | float | None) -> int | float:
    if configured_timeout is None:
        return ACTIVE_TIMEOUT_SECONDS
    return min(configured_timeout, ACTIVE_TIMEOUT_SECONDS)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

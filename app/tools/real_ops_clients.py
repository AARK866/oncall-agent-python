from typing import Any

import httpx

from app.config import settings


class PrometheusClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or settings.prometheus_base_url)
        self.timeout_seconds = timeout_seconds or settings.prometheus_timeout_seconds

    async def query(self, query: str) -> dict[str, Any]:
        base_url = _require_config(self.base_url, "PROMETHEUS_BASE_URL")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{base_url}/api/v1/query",
                params={"query": query},
            )
            response.raise_for_status()
            return response.json()


class LokiClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or settings.loki_base_url)
        self.timeout_seconds = timeout_seconds or settings.loki_timeout_seconds

    async def query_range(self, query: str, limit: int = 50) -> dict[str, Any]:
        base_url = _require_config(self.base_url, "LOKI_BASE_URL")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{base_url}/loki/api/v1/query_range",
                params={"query": query, "limit": limit},
            )
            response.raise_for_status()
            return response.json()


class GitLabClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        project_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or settings.gitlab_base_url)
        self.token = token or settings.gitlab_token
        self.project_id = project_id or settings.gitlab_project_id
        self.timeout_seconds = timeout_seconds or settings.gitlab_timeout_seconds

    async def list_deployments(self, environment: str | None = None, limit: int = 10) -> dict[str, Any]:
        base_url = _require_config(self.base_url, "GITLAB_BASE_URL")
        project_id = _require_config(self.project_id, "GITLAB_PROJECT_ID")
        headers = {}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token

        params: dict[str, Any] = {"per_page": limit}
        if environment:
            params["environment"] = environment

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{base_url}/api/v4/projects/{project_id}/deployments",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            return {"deployments": response.json()}


def _normalize_base_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


def _require_config(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required when OPS_TOOL_MODE=real.")
    return value

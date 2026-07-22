import base64
from typing import Any
from urllib.parse import quote

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


class GitHubClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
        timeout_seconds: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or settings.github_base_url)
        self.token = token or settings.github_token
        self.repo = repo or settings.github_repo
        self.branch = branch or settings.github_branch
        self.timeout_seconds = timeout_seconds or settings.github_timeout_seconds
        self.transport = transport

    async def list_commits(
        self,
        path: str | None = None,
        limit: int = 10,
        since: str | None = None,
    ) -> dict[str, Any]:
        repo = _require_config(self.repo, "GITHUB_REPO")
        params: dict[str, Any] = {
            "sha": self.branch,
            "per_page": max(1, min(limit, 100)),
        }
        if path:
            params["path"] = path
        if since:
            params["since"] = since

        commits = await self._get_json(f"/repos/{repo}/commits", params=params)
        return {
            "repo": repo,
            "branch": self.branch,
            "path": path,
            "commits": [_commit_summary(item) for item in commits],
        }

    async def get_commit(self, sha: str) -> dict[str, Any]:
        repo = _require_config(self.repo, "GITHUB_REPO")
        commit = await self._get_json(f"/repos/{repo}/commits/{sha}", params={})
        return {
            "repo": repo,
            "commit": _commit_detail(commit),
        }

    async def get_file(self, path: str, ref: str | None = None) -> dict[str, Any]:
        repo = _require_config(self.repo, "GITHUB_REPO")
        encoded_path = quote(path.strip("/"), safe="/")
        params = {"ref": ref or self.branch}
        data = await self._get_json(f"/repos/{repo}/contents/{encoded_path}", params=params)
        if isinstance(data, list):
            return {
                "repo": repo,
                "path": path,
                "ref": params["ref"],
                "type": "directory",
                "entries": [
                    {
                        "name": item.get("name"),
                        "path": item.get("path"),
                        "type": item.get("type"),
                        "size": item.get("size"),
                    }
                    for item in data
                ],
            }

        content = data.get("content", "")
        encoding = data.get("encoding")
        decoded_content = ""
        if encoding == "base64" and content:
            decoded_content = base64.b64decode(content).decode("utf-8", errors="replace")

        return {
            "repo": repo,
            "path": data.get("path", path),
            "ref": params["ref"],
            "type": data.get("type", "file"),
            "sha": data.get("sha"),
            "size": data.get("size"),
            "encoding": encoding,
            "content": decoded_content,
            "content_base64": content if encoding == "base64" else None,
        }

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        base_url = _require_config(self.base_url, "GITHUB_BASE_URL")
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.get(
                f"{base_url}{path}",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            return response.json()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def _commit_summary(item: dict[str, Any]) -> dict[str, Any]:
    commit = item.get("commit", {})
    author = commit.get("author", {}) or {}
    committer = commit.get("committer", {}) or {}
    return {
        "sha": item.get("sha"),
        "message": commit.get("message"),
        "author": {
            "name": author.get("name"),
            "email": author.get("email"),
            "date": author.get("date"),
        },
        "committer": {
            "name": committer.get("name"),
            "email": committer.get("email"),
            "date": committer.get("date"),
        },
        "html_url": item.get("html_url"),
    }


def _commit_detail(item: dict[str, Any]) -> dict[str, Any]:
    detail = _commit_summary(item)
    stats = item.get("stats", {}) or {}
    files = item.get("files", []) or []
    detail.update(
        {
            "stats": {
                "additions": stats.get("additions"),
                "deletions": stats.get("deletions"),
                "total": stats.get("total"),
            },
            "files": [
                {
                    "filename": file.get("filename"),
                    "status": file.get("status"),
                    "additions": file.get("additions"),
                    "deletions": file.get("deletions"),
                    "changes": file.get("changes"),
                    "patch": file.get("patch"),
                }
                for file in files
            ],
        }
    )
    return detail


def _normalize_base_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


def _require_config(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required when OPS_TOOL_MODE=real.")
    return value

from __future__ import annotations

import base64
import re
import time
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.config import settings

_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
)
_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


class ConnectorResponseError(RuntimeError):
    pass


class PrometheusClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        bearer_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(
            base_url or settings.prometheus_base_url
        )
        self.timeout_seconds = (
            timeout_seconds or settings.prometheus_timeout_seconds
        )
        self.bearer_token = (
            bearer_token
            if bearer_token is not None
            else settings.prometheus_bearer_token
        )
        self.username = (
            username
            if username is not None
            else settings.prometheus_username
        )
        self.password = (
            password
            if password is not None
            else settings.prometheus_password
        )
        self.verify_ssl = (
            settings.prometheus_verify_ssl
            if verify_ssl is None
            else verify_ssl
        )
        self.transport = transport

    async def query(self, query: str) -> dict[str, Any]:
        results = await self.query_many({"result": query})
        return results["result"]

    async def query_many(
        self,
        queries: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        base_url = _require_config(
            self.base_url,
            "PROMETHEUS_BASE_URL",
        )
        async with _http_client(
            timeout_seconds=self.timeout_seconds,
            headers=_bearer_headers(self.bearer_token),
            username=self.username,
            password=self.password,
            verify_ssl=self.verify_ssl,
            transport=self.transport,
        ) as client:
            results: dict[str, dict[str, Any]] = {}
            for name, query in queries.items():
                payload = await _get_json(
                    client,
                    f"{base_url}/api/v1/query",
                    provider="Prometheus",
                    params={"query": query},
                )
                results[name] = _validate_observability_payload(
                    payload,
                    provider="Prometheus",
                )
            return results


class LokiClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        bearer_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        org_id: str | None = None,
        verify_ssl: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(
            base_url or settings.loki_base_url
        )
        self.timeout_seconds = (
            timeout_seconds or settings.loki_timeout_seconds
        )
        self.bearer_token = (
            bearer_token
            if bearer_token is not None
            else settings.loki_bearer_token
        )
        self.username = (
            username if username is not None else settings.loki_username
        )
        self.password = (
            password if password is not None else settings.loki_password
        )
        self.org_id = (
            org_id if org_id is not None else settings.loki_org_id
        )
        self.verify_ssl = (
            settings.loki_verify_ssl
            if verify_ssl is None
            else verify_ssl
        )
        self.transport = transport

    async def ready(self) -> bool:
        base_url = _require_config(self.base_url, "LOKI_BASE_URL")
        headers = _bearer_headers(self.bearer_token)
        if self.org_id:
            headers["X-Scope-OrgID"] = self.org_id
        async with _http_client(
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            username=self.username,
            password=self.password,
            verify_ssl=self.verify_ssl,
            transport=self.transport,
        ) as client:
            response = await client.get(f"{base_url}/ready")
            response.raise_for_status()
            return response.text.strip().lower() in {
                "ready",
                "ok",
                "success",
            }

    async def query_range(
        self,
        query: str,
        limit: int = 50,
        window_seconds: int = 1800,
    ) -> dict[str, Any]:
        base_url = _require_config(self.base_url, "LOKI_BASE_URL")
        end_ns = time.time_ns()
        start_ns = end_ns - (max(60, window_seconds) * 1_000_000_000)
        headers = _bearer_headers(self.bearer_token)
        if self.org_id:
            headers["X-Scope-OrgID"] = self.org_id

        async with _http_client(
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            username=self.username,
            password=self.password,
            verify_ssl=self.verify_ssl,
            transport=self.transport,
        ) as client:
            payload = await _get_json(
                client,
                f"{base_url}/loki/api/v1/query_range",
                provider="Loki",
                params={
                    "query": query,
                    "limit": max(1, min(limit, 500)),
                    "start": start_ns,
                    "end": end_ns,
                    "direction": "backward",
                },
            )
        return _validate_observability_payload(
            payload,
            provider="Loki",
        )


class GitLabClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        project_id: str | None = None,
        timeout_seconds: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(
            base_url or settings.gitlab_base_url
        )
        self.token = token or settings.gitlab_token
        self.project_id = project_id or settings.gitlab_project_id
        self.timeout_seconds = (
            timeout_seconds or settings.gitlab_timeout_seconds
        )
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.project_id)

    async def list_deployments(
        self,
        environment: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        base_url = _require_config(
            self.base_url,
            "GITLAB_BASE_URL",
        )
        project_id = _require_config(
            self.project_id,
            "GITLAB_PROJECT_ID",
        )
        headers = {}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token

        params: dict[str, Any] = {
            "per_page": max(1, min(limit, 100)),
        }
        if environment:
            params["environment"] = environment

        async with _http_client(
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            payload = await _get_json(
                client,
                f"{base_url}/api/v4/projects/{project_id}/deployments",
                provider="GitLab",
                params=params,
            )
        if not isinstance(payload, list):
            raise ConnectorResponseError(
                "GitLab deployments response must be a list."
            )
        return {"deployments": payload}


class GitHubClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
        timeout_seconds: int | None = None,
        verify_ssl: bool | None = None,
        proxy_url: str | None = None,
        allowed_paths: str | list[str] | None = None,
        max_file_bytes: int | None = None,
        max_patch_chars: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(
            base_url or settings.github_base_url
        )
        self.token = token or settings.github_token
        self.repo = repo or settings.github_repo
        self.branch = _validate_ref(branch or settings.github_branch)
        self.timeout_seconds = (
            timeout_seconds or settings.github_timeout_seconds
        )
        self.verify_ssl = (
            settings.github_verify_ssl
            if verify_ssl is None
            else verify_ssl
        )
        self.proxy_url = _normalize_base_url(
            proxy_url or settings.github_proxy_url
        )
        raw_allowed_paths = (
            settings.github_allowed_paths
            if allowed_paths is None
            else allowed_paths
        )
        self.allowed_paths = _normalize_allowed_paths(
            raw_allowed_paths
        )
        self.max_file_bytes = max(
            1,
            max_file_bytes or settings.github_max_file_bytes,
        )
        self.max_patch_chars = max(
            1,
            max_patch_chars or settings.github_max_patch_chars,
        )
        self.transport = transport

    async def list_commits(
        self,
        path: str | None = None,
        limit: int = 10,
        since: str | None = None,
    ) -> dict[str, Any]:
        repo = _validated_repo(self.repo)
        normalized_path = (
            _validate_repository_path(path, self.allowed_paths)
            if path
            else None
        )
        normalized_since = _validate_since(since)
        params: dict[str, Any] = {
            "sha": self.branch,
            "per_page": max(1, min(limit, 100)),
        }
        if normalized_path:
            params["path"] = normalized_path
        if normalized_since:
            params["since"] = normalized_since

        commits = await self._get_json(
            f"/repos/{repo}/commits",
            params=params,
        )
        if not isinstance(commits, list):
            raise ConnectorResponseError(
                "GitHub commits response must be a list."
            )
        return {
            "repo": repo,
            "branch": self.branch,
            "path": normalized_path,
            "commits": [
                _commit_summary(item)
                for item in commits
                if isinstance(item, dict)
            ],
        }

    async def list_deployments(
        self,
        environment: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        repo = _validated_repo(self.repo)
        params: dict[str, Any] = {
            "ref": self.branch,
            "per_page": max(1, min(limit, 100)),
        }
        if environment:
            params["environment"] = str(environment).strip()
        deployments = await self._get_json(
            f"/repos/{repo}/deployments",
            params=params,
        )
        if not isinstance(deployments, list):
            raise ConnectorResponseError(
                "GitHub deployments response must be a list."
            )
        return {
            "repo": repo,
            "deployments": [
                _github_deployment(item)
                for item in deployments
                if isinstance(item, dict)
            ],
        }

    async def get_commit(self, sha: str) -> dict[str, Any]:
        repo = _validated_repo(self.repo)
        normalized_sha = str(sha).strip()
        if not _COMMIT_SHA_PATTERN.fullmatch(normalized_sha):
            raise ValueError(
                "GitHub commit SHA must contain 7 to 64 hexadecimal characters."
            )
        commit = await self._get_json(
            f"/repos/{repo}/commits/{normalized_sha}",
            params={},
        )
        if not isinstance(commit, dict):
            raise ConnectorResponseError(
                "GitHub commit response must be an object."
            )
        return {
            "repo": repo,
            "commit": _commit_detail(
                commit,
                max_patch_chars=self.max_patch_chars,
            ),
        }

    async def get_file(
        self,
        path: str,
        ref: str | None = None,
    ) -> dict[str, Any]:
        repo = _validated_repo(self.repo)
        normalized_path = _validate_repository_path(
            path,
            self.allowed_paths,
            allow_empty=True,
        )
        normalized_ref = _validate_ref(ref or self.branch)
        encoded_path = quote(normalized_path, safe="/")
        data = await self._get_json(
            f"/repos/{repo}/contents/{encoded_path}",
            params={"ref": normalized_ref},
        )
        if isinstance(data, list):
            return {
                "repo": repo,
                "path": normalized_path,
                "ref": normalized_ref,
                "type": "directory",
                "entries": [
                    {
                        "name": item.get("name"),
                        "path": item.get("path"),
                        "type": item.get("type"),
                        "size": item.get("size"),
                    }
                    for item in data[:1000]
                    if isinstance(item, dict)
                ],
            }
        if not isinstance(data, dict):
            raise ConnectorResponseError(
                "GitHub contents response must be an object or list."
            )

        declared_size = int(data.get("size") or 0)
        if declared_size > self.max_file_bytes:
            raise ValueError(
                "GitHub file exceeds GITHUB_MAX_FILE_BYTES."
            )
        content = data.get("content", "")
        encoding = data.get("encoding")
        decoded_content = ""
        if encoding == "base64" and content:
            if len(content) > (self.max_file_bytes * 2):
                raise ValueError(
                    "GitHub file exceeds GITHUB_MAX_FILE_BYTES."
                )
            decoded = base64.b64decode(content)
            if len(decoded) > self.max_file_bytes:
                raise ValueError(
                    "GitHub file exceeds GITHUB_MAX_FILE_BYTES."
                )
            decoded_content = decoded.decode(
                "utf-8",
                errors="replace",
            )

        return {
            "repo": repo,
            "path": data.get("path", normalized_path),
            "ref": normalized_ref,
            "type": data.get("type", "file"),
            "sha": data.get("sha"),
            "size": data.get("size"),
            "encoding": encoding,
            "content": decoded_content,
            "content_base64": (
                content if encoding == "base64" else None
            ),
        }

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any],
    ) -> Any:
        base_url = _require_config(
            self.base_url,
            "GITHUB_BASE_URL",
        )
        async with _http_client(
            timeout_seconds=self.timeout_seconds,
            headers=self._headers(),
            verify_ssl=self.verify_ssl,
            proxy_url=self.proxy_url,
            transport=self.transport,
        ) as client:
            return await _get_json(
                client,
                f"{base_url}{path}",
                provider="GitHub",
                params=params,
            )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def _http_client(
    *,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
    username: str | None = None,
    password: str | None = None,
    verify_ssl: bool = True,
    proxy_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    auth = None
    if username:
        if password is None:
            raise ValueError(
                "Connector password is required when username is configured."
            )
        auth = httpx.BasicAuth(username, password)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        headers=headers,
        auth=auth,
        verify=verify_ssl,
        proxy=proxy_url,
        transport=transport,
        limits=httpx.Limits(
            max_connections=max(1, settings.ops_http_max_connections),
            max_keepalive_connections=max(
                0,
                settings.ops_http_max_keepalive_connections,
            ),
        ),
        follow_redirects=False,
    )


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    provider: str,
    params: dict[str, Any],
) -> Any:
    response = await client.get(url, params=params)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise ConnectorResponseError(
            f"{provider} returned invalid JSON."
        ) from exc


def _validate_observability_payload(
    payload: Any,
    *,
    provider: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConnectorResponseError(
            f"{provider} response must be an object."
        )
    if payload.get("status") != "success":
        error_type = payload.get("errorType") or "unknown"
        error = payload.get("error") or "query failed"
        raise ConnectorResponseError(
            f"{provider} query failed ({error_type}): {error}"
        )
    if not isinstance(payload.get("data"), dict):
        raise ConnectorResponseError(
            f"{provider} response is missing data."
        )
    return payload


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


def _commit_detail(
    item: dict[str, Any],
    *,
    max_patch_chars: int,
) -> dict[str, Any]:
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
                _commit_file(file, max_patch_chars)
                for file in files[:300]
                if isinstance(file, dict)
            ],
        }
    )
    return detail


def _commit_file(
    file: dict[str, Any],
    max_patch_chars: int,
) -> dict[str, Any]:
    patch = file.get("patch")
    truncated = isinstance(patch, str) and len(patch) > max_patch_chars
    return {
        "filename": file.get("filename"),
        "status": file.get("status"),
        "additions": file.get("additions"),
        "deletions": file.get("deletions"),
        "changes": file.get("changes"),
        "patch": patch[:max_patch_chars] if truncated else patch,
        "patch_truncated": truncated,
    }


def _github_deployment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "environment": item.get("environment"),
        "version": item.get("ref"),
        "sha": item.get("sha"),
        "deployed_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "summary": (
            item.get("description")
            or item.get("task")
            or "GitHub deployment"
        ),
        "statuses_url": item.get("statuses_url"),
    }


def _normalize_base_url(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "Connector base URL must use http or https."
        )
    return normalized


def _validated_repo(value: str | None) -> str:
    repo = _require_config(value, "GITHUB_REPO")
    if not _REPOSITORY_PATTERN.fullmatch(repo):
        raise ValueError(
            "GITHUB_REPO must use the owner/repository format."
        )
    return repo


def _validate_repository_path(
    value: str,
    allowed_paths: tuple[str, ...],
    *,
    allow_empty: bool = False,
) -> str:
    normalized = str(value).strip().replace("\\", "/").strip("/")
    if not normalized and allow_empty:
        return ""
    if not normalized:
        raise ValueError("GitHub repository path is required.")
    if len(normalized) > 512:
        raise ValueError("GitHub repository path is too long.")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or "\x00" in normalized:
        raise ValueError("Unsafe GitHub repository path.")
    if allowed_paths and not any(
        normalized == prefix
        or normalized.startswith(f"{prefix}/")
        for prefix in allowed_paths
    ):
        raise ValueError(
            "GitHub repository path is outside GITHUB_ALLOWED_PATHS."
        )
    return normalized


def _normalize_allowed_paths(
    value: str | list[str],
) -> tuple[str, ...]:
    raw_values = value.split(",") if isinstance(value, str) else value
    return tuple(
        _validate_repository_path(item, ())
        for item in raw_values
        if str(item).strip()
    )


def _validate_ref(value: str) -> str:
    normalized = str(value).strip()
    if (
        not _REF_PATTERN.fullmatch(normalized)
        or ".." in normalized
        or "@{" in normalized
        or normalized.endswith(".lock")
    ):
        raise ValueError("Unsafe GitHub ref.")
    return normalized


def _validate_since(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "GitHub since must be an ISO-8601 timestamp."
        ) from exc
    return normalized


def _bearer_headers(token: str | None) -> dict[str, str]:
    return (
        {"Authorization": f"Bearer {token}"}
        if token
        else {}
    )


def _require_config(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(
            f"{name} is required when OPS_TOOL_MODE=real."
        )
    return value

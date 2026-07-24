import asyncio
import base64

import httpx
import pytest

from app.config import settings
from app.schemas import ToolCall
from app.tools import (
    GitHubClient,
    LokiClient,
    PrometheusClient,
    ToolRegistry,
    create_mock_ops_registry,
    create_ops_connector,
    create_ops_tool_registry,
    get_ops_tool_health,
)
from app.tools.real_ops_clients import ConnectorResponseError
from app.tools.real_ops_tools import RealOpsToolset, create_real_ops_tools


def test_mock_ops_registry_executes_metrics_tool() -> None:
    registry = create_mock_ops_registry()

    result = asyncio.run(
        registry.execute(ToolCall(name="query_metrics", arguments={"service": "payment-api"}))
    )

    assert result.success is True
    assert result.tool_name == "query_metrics"
    assert result.data["http_5xx_rate"] == "8.7%"


def test_tool_registry_returns_error_for_missing_tool() -> None:
    registry = create_mock_ops_registry()

    result = asyncio.run(registry.execute(ToolCall(name="missing_tool", arguments={})))

    assert result.success is False
    assert "Tool not found" in str(result.error)


def test_ops_tool_factory_creates_mock_connector_registry() -> None:
    registry = create_ops_tool_registry(mode="mock")

    assert registry.mode == "mock"
    assert registry.connector_name == "mock_ops"
    assert "query_logs" in registry.list_tools()
    assert registry.describe()["mode"] == "mock"


def test_ops_tool_factory_rejects_unknown_mode() -> None:
    try:
        create_ops_connector("unknown")
    except ValueError as exc:
        assert "Unsupported OPS_TOOL_MODE" in str(exc)
    else:
        raise AssertionError("Expected unsupported connector mode to fail")


def test_manual_tool_registry_keeps_default_metadata() -> None:
    registry = ToolRegistry()

    assert registry.describe()["connector_name"] == "manual"
    assert registry.describe()["mode"] == "manual"


def test_real_ops_connector_registers_expected_tools() -> None:
    registry = create_ops_tool_registry(mode="real")

    assert registry.mode == "real"
    assert registry.connector_name == "real_ops"
    assert registry.list_tools() == [
        "query_commit_detail",
        "query_deployments",
        "query_logs",
        "query_metrics",
        "query_recent_commits",
        "query_service_topology",
        "read_repository_file",
    ]


def test_real_ops_tool_reports_missing_prometheus_config() -> None:
    registry = create_ops_tool_registry(mode="real")

    result = asyncio.run(
        registry.execute(ToolCall(name="query_metrics", arguments={"service": "payment-api"}))
    )

    assert result.success is False
    assert "PROMETHEUS_BASE_URL" in str(result.error)


def test_real_topology_placeholder_is_available() -> None:
    tool_map = {tool.name: tool for tool in create_real_ops_tools()}

    result = asyncio.run(tool_map["query_service_topology"].run({"service": "payment-api"}))

    assert result["service"] == "payment-api"
    assert result["dependencies"] == []
    assert result["related_alerts"] == []


def test_ops_tool_health_reports_mock_ready() -> None:
    health = get_ops_tool_health(mode="mock")

    assert health.ready is True
    assert health.mode == "mock"
    assert health.backends[0].name == "mock_data"


def test_ops_tool_health_reports_real_missing_config() -> None:
    health = get_ops_tool_health(mode="real")

    assert health.ready is False
    missing = {
        setting
        for backend in health.backends
        for setting in backend.missing_settings
    }
    assert "PROMETHEUS_BASE_URL" in missing
    assert "LOKI_BASE_URL" in missing


def test_github_client_lists_commits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/service/commits"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "abc123",
                    "html_url": "https://github.com/acme/service/commit/abc123",
                    "commit": {
                        "message": "Fix payment timeout",
                        "author": {"name": "Ada", "email": "ada@example.com", "date": "2026-07-04T00:00:00Z"},
                        "committer": {"name": "Ada", "email": "ada@example.com", "date": "2026-07-04T00:00:00Z"},
                    },
                }
            ],
        )

    client = GitHubClient(
        base_url="https://api.github.test",
        token="test-token",
        repo="acme/service",
        branch="main",
        transport=httpx.MockTransport(handler),
    )

    data = asyncio.run(client.list_commits(limit=1))

    assert data["repo"] == "acme/service"
    assert data["commits"][0]["sha"] == "abc123"
    assert data["commits"][0]["message"] == "Fix payment timeout"


def test_github_client_reads_repository_file() -> None:
    encoded = base64.b64encode(b"print('hello')").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/service/contents/app/main.py"
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "app/main.py",
                "sha": "file-sha",
                "size": 14,
                "encoding": "base64",
                "content": encoded,
            },
        )

    client = GitHubClient(
        base_url="https://api.github.test",
        token="test-token",
        repo="acme/service",
        branch="main",
        transport=httpx.MockTransport(handler),
    )

    data = asyncio.run(client.get_file("app/main.py"))

    assert data["content"] == "print('hello')"
    assert data["content_base64"] == encoded
    assert data["path"] == "app/main.py"


def test_prometheus_client_sends_auth_and_validates_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        assert request.url.params["query"] == "up"
        assert request.headers["Authorization"] == "Bearer metrics-token"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": []},
            },
        )

    client = PrometheusClient(
        base_url="https://prometheus.test",
        bearer_token="metrics-token",
        transport=httpx.MockTransport(handler),
    )

    data = asyncio.run(client.query("up"))

    assert data["status"] == "success"
    assert data["data"]["result"] == []


def test_prometheus_client_rejects_failed_query_envelope() -> None:
    client = PrometheusClient(
        base_url="https://prometheus.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "status": "error",
                    "errorType": "bad_data",
                    "error": "invalid query",
                },
            )
        ),
    )

    with pytest.raises(
        ConnectorResponseError,
        match="invalid query",
    ):
        asyncio.run(client.query("broken"))


def test_loki_client_bounds_query_and_sends_tenant_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/loki/api/v1/query_range"
        assert request.headers["X-Scope-OrgID"] == "tenant-blue"
        assert request.headers["Authorization"] == "Bearer logs-token"
        assert request.url.params["direction"] == "backward"
        assert request.url.params["limit"] == "500"
        assert int(request.url.params["end"]) > int(
            request.url.params["start"]
        )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "streams", "result": []},
            },
        )

    client = LokiClient(
        base_url="https://loki.test",
        bearer_token="logs-token",
        org_id="tenant-blue",
        transport=httpx.MockTransport(handler),
    )

    data = asyncio.run(
        client.query_range(
            '{service="payment-api"}',
            limit=9999,
            window_seconds=3600,
        )
    )

    assert data["status"] == "success"


def test_real_tool_rejects_promql_label_injection() -> None:
    toolset = RealOpsToolset()

    with pytest.raises(ValueError, match="service must contain"):
        asyncio.run(
            toolset.query_metrics(
                {"service": 'payment-api"} or up{job="secret'}
            )
        )


def test_github_client_rejects_path_traversal() -> None:
    client = GitHubClient(
        base_url="https://api.github.test",
        repo="acme/service",
        branch="main",
    )

    with pytest.raises(ValueError, match="Unsafe GitHub"):
        asyncio.run(client.get_file("../secret.txt"))


def test_github_client_enforces_file_size_limit() -> None:
    client = GitHubClient(
        base_url="https://api.github.test",
        repo="acme/service",
        branch="main",
        max_file_bytes=10,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "type": "file",
                    "path": "large.log",
                    "size": 100,
                    "encoding": "base64",
                    "content": "",
                },
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="GITHUB_MAX_FILE_BYTES",
    ):
        asyncio.run(client.get_file("large.log"))


def test_deployment_tool_falls_back_to_github() -> None:
    class MissingGitLab:
        configured = False

    class FakeGitHub:
        async def list_deployments(self, environment=None, limit=10):
            assert environment == "production"
            assert limit == 10
            return {
                "deployments": [
                    {
                        "version": "main",
                        "deployed_at": "2026-07-24T00:00:00Z",
                    }
                ]
            }

    toolset = RealOpsToolset(
        gitlab=MissingGitLab(),
        github=FakeGitHub(),
    )

    result = asyncio.run(
        toolset.query_deployments(
            {
                "service": "payment-api",
                "environment": "production",
            }
        )
    )

    assert result["provider"] == "github"
    assert result["deployments"][0]["version"] == "main"


def test_real_health_treats_gitlab_as_optional(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "prometheus_base_url",
        "https://prometheus.test",
    )
    monkeypatch.setattr(
        settings,
        "loki_base_url",
        "https://loki.test",
    )
    monkeypatch.setattr(settings, "github_repo", "acme/service")

    health = get_ops_tool_health(mode="real")

    assert health.ready is True
    gitlab = next(
        backend
        for backend in health.backends
        if backend.name == "gitlab"
    )
    assert gitlab.required is False
    assert gitlab.configured is False

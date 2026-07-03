import asyncio

from app.schemas import ToolCall
from app.tools import ToolRegistry, create_mock_ops_registry, create_ops_connector, create_ops_tool_registry


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

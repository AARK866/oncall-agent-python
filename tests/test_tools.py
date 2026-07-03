import asyncio

from app.schemas import ToolCall
from app.tools import create_mock_ops_registry


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

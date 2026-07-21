import asyncio

from app.reliability import RetryPolicy
from app.schemas import ToolCall
from app.tools import ToolRegistry
from app.tools.base import SimpleTool


def test_tool_registry_retries_transient_tool_failure() -> None:
    attempts = {"count": 0}

    async def flaky_handler(arguments):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("temporary timeout")
        return {"ok": True}

    registry = ToolRegistry(
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )
    registry.register(SimpleTool(name="flaky", description="flaky", handler=flaky_handler))

    result = asyncio.run(registry.execute(ToolCall(name="flaky", arguments={})))

    assert result.success is True
    assert attempts["count"] == 3
    assert result.data["ok"] is True
    assert result.data["_retry"]["attempts"] == 3
    assert result.data["_retry"]["retried"] is True


def test_tool_registry_does_not_retry_non_retryable_failure() -> None:
    attempts = {"count": 0}

    async def invalid_handler(arguments):
        attempts["count"] += 1
        raise ValueError("bad arguments")

    registry = ToolRegistry(
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )
    registry.register(SimpleTool(name="invalid", description="invalid", handler=invalid_handler))

    result = asyncio.run(registry.execute(ToolCall(name="invalid", arguments={})))

    assert result.success is False
    assert attempts["count"] == 1
    assert "bad arguments" in str(result.error)
    assert result.data["_retry"]["attempts"] == 1
    assert result.data["_retry"]["retried"] is False


def test_tool_registry_stops_after_retry_budget() -> None:
    attempts = {"count": 0}

    async def always_timeout(arguments):
        attempts["count"] += 1
        raise TimeoutError("still down")

    registry = ToolRegistry(
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    registry.register(SimpleTool(name="down", description="down", handler=always_timeout))

    result = asyncio.run(registry.execute(ToolCall(name="down", arguments={})))

    assert result.success is False
    assert attempts["count"] == 2
    assert result.data["_retry"]["attempts"] == 2
    assert result.data["_retry"]["retried"] is True

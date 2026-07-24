import logging
from time import perf_counter

from app.observability.metrics import observe_tool_call
from app.reliability import RetryError, RetryPolicy, run_with_retry
from app.schemas import ToolCall, ToolResult
from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(
        self,
        connector_name: str = "manual",
        mode: str = "manual",
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.connector_name = connector_name
        self.mode = mode
        self.retry_policy = retry_policy or RetryPolicy.from_settings()
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def tool_schemas(self) -> list[dict]:
        return [self._tools[name].to_schema() for name in self.list_tools()]

    def describe(self) -> dict[str, object]:
        return {
            "connector_name": self.connector_name,
            "mode": self.mode,
            "tools": self.tool_schemas(),
        }

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self.get(tool_call.name)
        if tool is None:
            observe_tool_call(
                tool_name=tool_call.name,
                connector=self.connector_name,
                success=False,
                duration_seconds=0,
            )
            return ToolResult(
                tool_name=tool_call.name,
                success=False,
                error=f"Tool not found: {tool_call.name}",
            )

        started_at = perf_counter()
        try:
            outcome = await run_with_retry(
                operation=lambda: tool.run(tool_call.arguments),
                policy=self.retry_policy,
            )
            data = self._with_retry_metadata(outcome.value, outcome.metadata())
            elapsed_seconds = perf_counter() - started_at
            result = ToolResult(
                tool_name=tool_call.name,
                success=True,
                data=data,
                elapsed_ms=int(elapsed_seconds * 1000),
            )
            observe_tool_call(
                tool_name=tool_call.name,
                connector=self.connector_name,
                success=True,
                duration_seconds=elapsed_seconds,
            )
            logger.info(
                "Agent tool completed.",
                extra={
                    "event": "tool.call",
                    "outcome": "success",
                    "tool_name": tool_call.name,
                    "duration_ms": result.elapsed_ms,
                },
            )
            return result
        except RetryError as exc:
            elapsed_seconds = perf_counter() - started_at
            result = ToolResult(
                tool_name=tool_call.name,
                success=False,
                data={"_retry": exc.metadata()},
                error=str(exc.last_error),
                elapsed_ms=int(elapsed_seconds * 1000),
            )
            observe_tool_call(
                tool_name=tool_call.name,
                connector=self.connector_name,
                success=False,
                duration_seconds=elapsed_seconds,
            )
            logger.warning(
                "Agent tool failed.",
                extra={
                    "event": "tool.call",
                    "outcome": "failure",
                    "tool_name": tool_call.name,
                    "duration_ms": result.elapsed_ms,
                },
            )
            return result

    def _elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)

    def _with_retry_metadata(
        self,
        data: dict,
        metadata: dict[str, object],
    ) -> dict:
        if int(metadata.get("attempts", 1)) <= 1:
            return data
        return {**data, "_retry": metadata}

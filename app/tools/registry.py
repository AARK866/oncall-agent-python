from time import perf_counter

from app.schemas import ToolCall, ToolResult
from app.tools.base import BaseTool


class ToolRegistry:
    def __init__(self, connector_name: str = "manual", mode: str = "manual") -> None:
        self.connector_name = connector_name
        self.mode = mode
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
            return ToolResult(
                tool_name=tool_call.name,
                success=False,
                error=f"Tool not found: {tool_call.name}",
            )

        started_at = perf_counter()
        try:
            data = await tool.run(tool_call.arguments)
            return ToolResult(
                tool_name=tool_call.name,
                success=True,
                data=data,
                elapsed_ms=self._elapsed_ms(started_at),
            )
        except Exception as exc:
            return ToolResult(
                tool_name=tool_call.name,
                success=False,
                error=str(exc),
                elapsed_ms=self._elapsed_ms(started_at),
            )

    def _elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)

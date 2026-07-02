import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any


ToolHandler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


class BaseTool(ABC):
    name: str
    description: str
    parameters_schema: dict[str, Any]

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class SimpleTool(BaseTool):
    def __init__(
        self,
        name: str,
        description: str,
        handler: ToolHandler,
        parameters_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.handler = handler
        self.parameters_schema = parameters_schema or {
            "type": "object",
            "properties": {},
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.handler(arguments)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, dict):
            return result

        return {"result": result}

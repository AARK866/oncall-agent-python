from abc import ABC, abstractmethod

from app.tools.base import BaseTool
from app.tools.mock_ops_tools import create_mock_ops_tools
from app.tools.real_ops_tools import create_real_ops_tools
from app.tools.registry import ToolRegistry


class OpsToolConnector(ABC):
    """Connector that provides a group of ops tools from one backend."""

    mode: str
    name: str

    @abstractmethod
    def tools(self) -> list[BaseTool]:
        raise NotImplementedError

    def create_registry(self) -> ToolRegistry:
        registry = ToolRegistry(connector_name=self.name, mode=self.mode)
        for tool in self.tools():
            registry.register(tool)
        return registry


class MockOpsConnector(OpsToolConnector):
    mode = "mock"
    name = "mock_ops"

    def tools(self) -> list[BaseTool]:
        return create_mock_ops_tools()


class RealOpsConnector(OpsToolConnector):
    mode = "real"
    name = "real_ops"

    def tools(self) -> list[BaseTool]:
        return create_real_ops_tools()

from app.config import settings
from app.tools.connectors import MockOpsConnector, OpsToolConnector, RealOpsConnector
from app.tools.registry import ToolRegistry


def create_ops_tool_registry(mode: str | None = None) -> ToolRegistry:
    connector = create_ops_connector(mode or settings.ops_tool_mode)
    return connector.create_registry()


def create_ops_connector(mode: str) -> OpsToolConnector:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "mock":
        return MockOpsConnector()
    if normalized_mode == "real":
        return RealOpsConnector()
    raise ValueError(f"Unsupported OPS_TOOL_MODE: {mode}")

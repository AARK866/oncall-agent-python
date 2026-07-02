import json
from pathlib import Path
from typing import Any

from app.tools.base import SimpleTool
from app.tools.registry import ToolRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def create_mock_ops_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in create_mock_ops_tools():
        registry.register(tool)
    return registry


def create_mock_ops_tools() -> list[SimpleTool]:
    return [
        SimpleTool(
            name="query_metrics",
            description="查询服务最近一段时间的错误率、延迟、CPU、内存等指标。",
            handler=query_metrics,
            parameters_schema=_service_window_schema(),
        ),
        SimpleTool(
            name="query_logs",
            description="查询服务最近一段时间的关键日志和异常日志。",
            handler=query_logs,
            parameters_schema=_service_window_schema(),
        ),
        SimpleTool(
            name="query_deployments",
            description="查询服务最近的发布记录。",
            handler=query_deployments,
            parameters_schema=_service_window_schema(),
        ),
        SimpleTool(
            name="query_service_topology",
            description="查询服务上下游依赖和相邻告警。",
            handler=query_service_topology,
            parameters_schema=_service_schema(),
        ),
    ]


def query_metrics(arguments: dict[str, Any]) -> dict[str, Any]:
    service = _normalize_service(arguments.get("service"))
    metrics = _load_json("mock_metrics.json")
    return metrics.get(service, _not_found(service, "metrics"))


def query_logs(arguments: dict[str, Any]) -> dict[str, Any]:
    service = _normalize_service(arguments.get("service"))
    logs = _load_json("mock_logs.json")
    return {
        "service": service,
        "logs": logs.get(service, []),
        "summary": f"找到 {len(logs.get(service, []))} 条 {service} 相关日志。",
    }


def query_deployments(arguments: dict[str, Any]) -> dict[str, Any]:
    service = _normalize_service(arguments.get("service"))
    deployments = _load_json("mock_deployments.json")
    return {
        "service": service,
        "deployments": deployments.get(service, []),
        "summary": f"找到 {len(deployments.get(service, []))} 条 {service} 发布记录。",
    }


def query_service_topology(arguments: dict[str, Any]) -> dict[str, Any]:
    service = _normalize_service(arguments.get("service"))
    topology = _load_json("mock_topology.json")
    data = topology.get(service)
    if data is None:
        return _not_found(service, "topology")
    return {"service": service, **data}


def _load_json(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_service(service: Any) -> str:
    if not service:
        return "payment-api"
    return str(service).strip()


def _not_found(service: str, data_type: str) -> dict[str, Any]:
    return {
        "service": service,
        "summary": f"未找到 {service} 的 mock {data_type} 数据。",
    }


def _service_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "服务名，例如 payment-api。",
            }
        },
        "required": ["service"],
    }


def _service_window_schema() -> dict[str, Any]:
    schema = _service_schema()
    schema["properties"]["window"] = {
        "type": "string",
        "description": "查询时间窗口，例如 30m、1h。",
        "default": "30m",
    }
    return schema

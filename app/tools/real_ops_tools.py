from typing import Any

from app.tools.base import SimpleTool
from app.tools.real_ops_clients import GitLabClient, LokiClient, PrometheusClient


class RealOpsToolset:
    def __init__(
        self,
        prometheus: PrometheusClient | None = None,
        loki: LokiClient | None = None,
        gitlab: GitLabClient | None = None,
    ) -> None:
        self.prometheus = prometheus or PrometheusClient()
        self.loki = loki or LokiClient()
        self.gitlab = gitlab or GitLabClient()

    def tools(self) -> list[SimpleTool]:
        return [
            SimpleTool(
                name="query_metrics",
                description="Query real Prometheus metrics for service errors, latency, and resource pressure.",
                handler=self.query_metrics,
                parameters_schema=_service_window_schema(),
            ),
            SimpleTool(
                name="query_logs",
                description="Query real Loki logs for service errors in a recent time window.",
                handler=self.query_logs,
                parameters_schema=_service_window_schema(),
            ),
            SimpleTool(
                name="query_deployments",
                description="Query real GitLab deployment records for the service project.",
                handler=self.query_deployments,
                parameters_schema=_deployment_schema(),
            ),
            SimpleTool(
                name="query_service_topology",
                description="Return currently known topology placeholder for real ops mode.",
                handler=self.query_service_topology,
                parameters_schema=_service_schema(),
            ),
        ]

    async def query_metrics(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = _normalize_service(arguments.get("service"))
        five_xx_query = f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[5m]))'
        p95_query = (
            "histogram_quantile(0.95, "
            f'sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))'
        )
        return {
            "service": service,
            "provider": "prometheus",
            "queries": {
                "http_5xx_rate": five_xx_query,
                "p95_latency": p95_query,
            },
            "http_5xx_rate": await self.prometheus.query(five_xx_query),
            "p95_latency": await self.prometheus.query(p95_query),
            "summary": f"Queried Prometheus metrics for {service}.",
        }

    async def query_logs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = _normalize_service(arguments.get("service"))
        limit = int(arguments.get("limit") or 50)
        query = f'{{service="{service}"}} |= "ERROR"'
        return {
            "service": service,
            "provider": "loki",
            "query": query,
            "logs": await self.loki.query_range(query=query, limit=limit),
            "summary": f"Queried Loki logs for {service}.",
        }

    async def query_deployments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = _normalize_service(arguments.get("service"))
        environment = arguments.get("environment")
        limit = int(arguments.get("limit") or 10)
        return {
            "service": service,
            "provider": "gitlab",
            **await self.gitlab.list_deployments(environment=environment, limit=limit),
            "summary": f"Queried GitLab deployments for {service}.",
        }

    async def query_service_topology(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = _normalize_service(arguments.get("service"))
        return {
            "service": service,
            "provider": "topology-placeholder",
            "dependencies": [],
            "related_alerts": [],
            "summary": (
                "Real topology lookup is not configured yet. "
                "Add a CMDB, Kubernetes, or service graph client in the next connector phase."
            ),
        }


def create_real_ops_tools(
    prometheus: PrometheusClient | None = None,
    loki: LokiClient | None = None,
    gitlab: GitLabClient | None = None,
) -> list[SimpleTool]:
    return RealOpsToolset(
        prometheus=prometheus,
        loki=loki,
        gitlab=gitlab,
    ).tools()


def _normalize_service(service: Any) -> str:
    if not service:
        return "payment-api"
    return str(service).strip()


def _service_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "Service name, for example payment-api.",
            }
        },
        "required": ["service"],
    }


def _service_window_schema() -> dict[str, Any]:
    schema = _service_schema()
    schema["properties"]["window"] = {
        "type": "string",
        "description": "Time window, for example 30m or 1h. Real query templates currently use recent data.",
        "default": "30m",
    }
    return schema


def _deployment_schema() -> dict[str, Any]:
    schema = _service_window_schema()
    schema["properties"]["environment"] = {
        "type": "string",
        "description": "Optional GitLab environment name, for example production.",
    }
    schema["properties"]["limit"] = {
        "type": "integer",
        "description": "Maximum deployment records to fetch.",
        "default": 10,
    }
    return schema

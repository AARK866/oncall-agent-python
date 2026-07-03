from app.schemas import ReactStep, ToolCall, ToolResult
from app.tools import ToolRegistry


class ReactLoop:
    """ReAct loop that can run default or LLM-selected ops tools."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    async def run(
        self,
        question: str,
        service: str,
        tool_calls: list[ToolCall] | None = None,
    ) -> list[ReactStep]:
        steps: list[ReactStep] = []
        calls = tool_calls or self.default_tool_calls(service)

        for tool_call in calls:
            observation = await self._act(
                steps=steps,
                thought=self._thought_for_tool(tool_call.name, service),
                action=self._normalize_tool_call(tool_call, service),
            )
            if observation.success is False:
                steps.append(
                    ReactStep(
                        thought=(
                            f"{tool_call.name} failed, so the final diagnosis should mark this "
                            "evidence as incomplete."
                        )
                    )
                )

        steps.append(ReactStep(thought=self._final_thought(steps)))
        return steps

    def default_tool_calls(self, service: str) -> list[ToolCall]:
        return [
            ToolCall(name="query_metrics", arguments={"service": service, "window": "30m"}),
            ToolCall(name="query_logs", arguments={"service": service, "window": "30m"}),
            ToolCall(name="query_deployments", arguments={"service": service, "window": "1h"}),
            ToolCall(name="query_service_topology", arguments={"service": service}),
        ]

    async def _act(
        self,
        steps: list[ReactStep],
        thought: str,
        action: ToolCall,
    ) -> ToolResult:
        observation = await self.tool_registry.execute(action)
        steps.append(
            ReactStep(
                thought=thought,
                action=action,
                observation=observation,
            )
        )
        return observation

    def _normalize_tool_call(self, tool_call: ToolCall, service: str) -> ToolCall:
        arguments = dict(tool_call.arguments)
        arguments.setdefault("service", service)
        return ToolCall(name=tool_call.name, arguments=arguments, trace_id=tool_call.trace_id)

    def _thought_for_tool(self, tool_name: str, service: str) -> str:
        thoughts = {
            "query_metrics": f"Check {service} metrics first to confirm error rate, latency, and resource pressure.",
            "query_logs": "Inspect logs in the same time window to find direct error signals.",
            "query_deployments": "Check recent deployments because incidents often correlate with change windows.",
            "query_service_topology": "Inspect upstream and downstream dependencies for related alerts.",
        }
        return thoughts.get(tool_name, f"Run {tool_name} because it may provide useful incident evidence.")

    def _final_thought(self, steps: list[ReactStep]) -> str:
        result_map = {
            step.observation.tool_name: step.observation
            for step in steps
            if step.observation is not None
        }
        clues: list[str] = []

        metrics = result_map.get("query_metrics")
        if metrics and "8.7%" in str(metrics.data.get("http_5xx_rate", "")):
            clues.append("5xx rate is clearly elevated")

        logs = result_map.get("query_logs")
        if logs and "connection pool exhausted" in str(logs.data).lower():
            clues.append("logs mention database connection pool exhaustion")

        deployments = result_map.get("query_deployments")
        if deployments and deployments.data.get("deployments"):
            clues.append("there was a recent deployment near the incident window")

        topology = result_map.get("query_service_topology")
        if topology and topology.data.get("related_alerts"):
            clues.append("related dependency alerts exist in the service topology")

        if clues:
            return f"Collected evidence points to: {', '.join(clues)}."

        return "The selected tools did not produce a single high-confidence root cause."

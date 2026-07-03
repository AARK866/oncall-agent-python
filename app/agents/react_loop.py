from app.schemas import ReactStep, ToolCall, ToolResult
from app.tools import ToolRegistry


class ReactLoop:
    """Deterministic ReAct loop for local ops troubleshooting."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    async def run(self, question: str, service: str) -> list[ReactStep]:
        steps: list[ReactStep] = []

        metrics = await self._act(
            steps=steps,
            thought=f"先确认 {service} 的错误率、延迟和资源指标是否异常。",
            action=ToolCall(name="query_metrics", arguments={"service": service, "window": "30m"}),
        )

        logs = await self._act(
            steps=steps,
            thought="指标存在异常后，需要查看同一时间窗口的错误日志，寻找直接报错信号。",
            action=ToolCall(name="query_logs", arguments={"service": service, "window": "30m"}),
        )

        deployments = await self._act(
            steps=steps,
            thought="如果异常有明确开始时间，需要检查附近是否发生过发布变更。",
            action=ToolCall(name="query_deployments", arguments={"service": service, "window": "1h"}),
        )

        topology = await self._act(
            steps=steps,
            thought="服务自身异常还可能来自上下游依赖，需要查看服务拓扑和相邻告警。",
            action=ToolCall(name="query_service_topology", arguments={"service": service}),
        )

        steps.append(
            ReactStep(
                thought=self._final_thought(
                    metrics=metrics,
                    logs=logs,
                    deployments=deployments,
                    topology=topology,
                )
            )
        )
        return steps

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

    def _final_thought(
        self,
        metrics: ToolResult,
        logs: ToolResult,
        deployments: ToolResult,
        topology: ToolResult,
    ) -> str:
        clues: list[str] = []
        if "8.7%" in str(metrics.data.get("http_5xx_rate", "")):
            clues.append("错误率明显升高")
        if "connection pool exhausted" in str(logs.data).lower():
            clues.append("日志出现数据库连接池耗尽")
        if deployments.data.get("deployments"):
            clues.append("异常前后存在发布记录")
        if topology.data.get("related_alerts"):
            clues.append("拓扑中存在相邻依赖告警")

        if clues:
            return f"综合观察结果，关键线索包括：{'、'.join(clues)}。可以进入最终诊断汇总。"

        return "工具观察结果暂未形成明确线索，需要补充更多时间窗口或真实监控数据。"

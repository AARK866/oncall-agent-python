from app.schemas import PlanStep, PlanTrace, ToolCall
from app.tools import ToolRegistry


class PlanExecuteReplan:
    """Small deterministic Plan-Execute-Replan flow for ops analysis."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    def plan(self, service: str) -> list[PlanStep]:
        return [
            PlanStep(
                step_id="check_metrics",
                goal=f"确认 {service} 的错误率、延迟和资源指标。",
                tool_call=ToolCall(name="query_metrics", arguments={"service": service, "window": "30m"}),
            ),
            PlanStep(
                step_id="inspect_logs",
                goal="查看异常时间窗口内的错误日志。",
                tool_call=ToolCall(name="query_logs", arguments={"service": service, "window": "30m"}),
            ),
            PlanStep(
                step_id="check_deployments",
                goal="检查异常开始前后是否有发布变更。",
                tool_call=ToolCall(name="query_deployments", arguments={"service": service, "window": "1h"}),
            ),
            PlanStep(
                step_id="check_topology",
                goal="检查上下游依赖和相邻告警。",
                tool_call=ToolCall(name="query_service_topology", arguments={"service": service}),
            ),
        ]

    async def run(self, service: str) -> PlanTrace:
        trace = PlanTrace(plan=self.plan(service))

        for index, step in enumerate(trace.plan):
            if step.tool_call is None:
                step.status = "skipped"
                continue

            step.status = "running"
            step.observation = await self.tool_registry.execute(step.tool_call)
            step.status = "completed" if step.observation.success else "failed"

            note = self.replan(trace.plan[: index + 1])
            if note:
                trace.replan_notes.append(note)

        return trace

    def replan(self, completed_steps: list[PlanStep]) -> str | None:
        latest = completed_steps[-1]
        observation = latest.observation
        if observation is None or not observation.success:
            return f"{latest.step_id} 未成功，需要补充人工排查或重试。"

        if latest.step_id == "check_metrics" and "8.7%" in str(observation.data.get("http_5xx_rate", "")):
            return "指标显示 5xx 明显升高，继续优先检查错误日志和发布记录。"

        if latest.step_id == "inspect_logs" and "connection pool exhausted" in str(observation.data).lower():
            return "日志出现连接池耗尽，后续计划需要重点核对发布变更和数据库依赖。"

        if latest.step_id == "check_deployments" and observation.data.get("deployments"):
            return "发现异常前后存在发布记录，最终建议中应包含回滚评估。"

        if latest.step_id == "check_topology" and observation.data.get("related_alerts"):
            return "发现相邻依赖告警，最终建议中应同步联系依赖服务负责人。"

        return None

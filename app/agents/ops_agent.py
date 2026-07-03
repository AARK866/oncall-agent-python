from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.plan_execute import PlanExecuteReplan
from app.agents.react_loop import ReactLoop
from app.schemas import ChatMode, ChatResponse, DiagnosisReport, ToolResult
from app.tools import ToolRegistry, create_mock_ops_registry


class OpsAgent:
    """Basic deterministic ops agent that runs a fixed investigation playbook."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        knowledge_agent: KnowledgeAgent,
    ) -> None:
        self.tool_registry = tool_registry
        self.knowledge_agent = knowledge_agent
        self.react_loop = ReactLoop(tool_registry=tool_registry)
        self.plan_execute = PlanExecuteReplan(tool_registry=tool_registry)

    @classmethod
    def create_default(cls) -> "OpsAgent":
        return cls(
            tool_registry=create_mock_ops_registry(),
            knowledge_agent=KnowledgeAgent.from_runbook_directory(),
        )

    async def analyze(
        self,
        question: str,
        session_id: str = "default",
        service: str | None = None,
    ) -> ChatResponse:
        target_service = service or self._infer_service(question)
        plan_trace = await self.plan_execute.run(service=target_service)
        react_steps = await self.react_loop.run(question=question, service=target_service)
        tool_results = [
            step.observation
            for step in react_steps
            if step.observation is not None
        ]
        knowledge_response = await self.knowledge_agent.answer(
            question=question,
            session_id=session_id,
            top_k=2,
            service=target_service,
            incident_type="5xx" if "5xx" in question.lower() else None,
        )
        report = self._build_report(
            service=target_service,
            tool_results=tool_results,
            runbook_answer=knowledge_response.answer,
        )

        return ChatResponse(
            session_id=session_id,
            answer=self._format_report(report),
            mode=ChatMode.ops,
            sources=knowledge_response.sources,
            metadata={
                "service": target_service,
                "tool_results": [result.model_dump() for result in tool_results],
                "react_steps": [step.model_dump() for step in react_steps],
                "plan_trace": plan_trace.model_dump(),
                "runbook_retrieved_count": knowledge_response.metadata.get("retrieved_count", 0),
            },
        )

    def _infer_service(self, question: str) -> str:
        normalized = question.lower()
        if "payment" in normalized or "支付" in normalized:
            return "payment-api"
        if "order" in normalized or "订单" in normalized:
            return "order-api"
        return "payment-api"

    def _build_report(
        self,
        service: str,
        tool_results: list[ToolResult],
        runbook_answer: str,
    ) -> DiagnosisReport:
        result_map = {result.tool_name: result for result in tool_results}
        metrics = result_map.get("query_metrics")
        logs = result_map.get("query_logs")
        deployments = result_map.get("query_deployments")
        topology = result_map.get("query_service_topology")

        evidence = self._collect_evidence(metrics, logs, deployments, topology)
        recommendations = self._collect_recommendations(metrics, logs, deployments, topology)
        risks = [
            "回滚、重启、扩容等高风险操作需要人工确认。",
            "当前结论基于 mock 数据和固定排查流程，真实环境需要结合实时监控确认。",
        ]

        return DiagnosisReport(
            summary=self._build_summary(service, metrics, logs, deployments, topology),
            evidence=evidence,
            recommendations=recommendations,
            risks=risks,
            confidence=0.78,
        )

    def _build_summary(
        self,
        service: str,
        metrics: ToolResult | None,
        logs: ToolResult | None,
        deployments: ToolResult | None,
        topology: ToolResult | None,
    ) -> str:
        has_high_5xx = bool(metrics and "8.7%" in str(metrics.data.get("http_5xx_rate", "")))
        has_db_errors = bool(logs and "connection pool exhausted" in str(logs.data).lower())
        has_recent_deploy = bool(deployments and deployments.data.get("deployments"))
        has_db_alert = bool(topology and "mysql-payment" in str(topology.data))

        if has_high_5xx and has_db_errors and has_recent_deploy:
            return (
                f"初步判断 {service} 的 5xx 升高与最近发布及数据库连接池配置相关，"
                "需要优先检查发布变更和连接池参数。"
            )

        if has_high_5xx and has_db_alert:
            return f"初步判断 {service} 异常可能与数据库连接资源紧张有关。"

        return f"已完成 {service} 的基础排查，暂未形成高置信度单一根因。"

    def _collect_evidence(
        self,
        metrics: ToolResult | None,
        logs: ToolResult | None,
        deployments: ToolResult | None,
        topology: ToolResult | None,
    ) -> list[str]:
        evidence: list[str] = []
        if metrics and metrics.success:
            evidence.append(str(metrics.data.get("summary", "指标查询成功。")))
        if logs and logs.success:
            evidence.append(str(logs.data.get("summary", "日志查询成功。")))
            for item in logs.data.get("logs", [])[:2]:
                evidence.append(f"{item.get('timestamp')} {item.get('level')}: {item.get('message')}")
        if deployments and deployments.success:
            deployments_data = deployments.data.get("deployments", [])
            if deployments_data:
                latest = deployments_data[0]
                evidence.append(
                    f"{latest.get('deployed_at')} 发布 {latest.get('version')}：{latest.get('summary')}"
                )
        if topology and topology.success:
            related_alerts = topology.data.get("related_alerts", [])
            if related_alerts:
                evidence.append(f"发现相邻告警：{related_alerts[0].get('title')}")

        return evidence

    def _collect_recommendations(
        self,
        metrics: ToolResult | None,
        logs: ToolResult | None,
        deployments: ToolResult | None,
        topology: ToolResult | None,
    ) -> list[str]:
        recommendations = [
            "继续观察错误率、P95 延迟和核心支付成功率。",
            "保留现场日志，避免排查过程中丢失关键证据。",
        ]

        if deployments and deployments.data.get("deployments"):
            recommendations.insert(0, "优先评估最近发布是否需要回滚到上一个稳定版本。")
        if logs and "connection pool exhausted" in str(logs.data).lower():
            recommendations.insert(0, "检查数据库连接池配置，必要时临时调大连接池并观察恢复情况。")
        if topology and topology.data.get("related_alerts"):
            recommendations.append("同步联系相关下游服务或数据库负责人确认相邻告警。")

        return recommendations

    def _format_report(self, report: DiagnosisReport) -> str:
        sections = [
            "诊断结论：",
            report.summary,
            "",
            "证据：",
            *[f"- {item}" for item in report.evidence],
            "",
            "建议：",
            *[f"- {item}" for item in report.recommendations],
            "",
            "风险：",
            *[f"- {item}" for item in report.risks],
            "",
            f"置信度：{report.confidence}",
        ]
        return "\n".join(sections)

from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.llm_ops_assistant import LLMOpsAssistant
from app.agents.ops_graph import OpsGraphWorkflow
from app.agents.plan_execute import PlanExecuteReplan
from app.agents.react_loop import ReactLoop
from app.config import settings
from app.llm import LLMClient, create_llm_client
from app.schemas import ChatMode, ChatResponse, DiagnosisReport, ToolResult
from app.storage import SQLiteIncidentStore
from app.tools import ToolRegistry, create_ops_tool_registry


class OpsAgent:
    """Basic deterministic ops agent that runs a fixed investigation playbook."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        knowledge_agent: KnowledgeAgent,
        incident_store: SQLiteIncidentStore | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.knowledge_agent = knowledge_agent
        self.incident_store = incident_store
        self.llm = llm or create_llm_client()
        self.react_loop = ReactLoop(tool_registry=tool_registry)
        self.plan_execute = PlanExecuteReplan(tool_registry=tool_registry)
        self.llm_ops_assistant = LLMOpsAssistant(llm=self.llm, tool_registry=tool_registry)
        self.graph = OpsGraphWorkflow(
            tool_registry=self.tool_registry,
            knowledge_agent=self.knowledge_agent,
            react_loop=self.react_loop,
            plan_execute=self.plan_execute,
            llm_ops_assistant=self.llm_ops_assistant,
            infer_service=self._infer_service,
            build_report=self._build_report,
            format_report=self._format_report,
            persist_analysis=self._persist_analysis,
            graph_runtime=settings.ops_graph_runtime,
        )

    @classmethod
    def create_default(
        cls,
        incident_store: SQLiteIncidentStore | None = None,
        llm: LLMClient | None = None,
    ) -> "OpsAgent":
        return cls(
            tool_registry=create_ops_tool_registry(),
            knowledge_agent=KnowledgeAgent.from_runbook_directory(),
            incident_store=incident_store or SQLiteIncidentStore.from_settings(),
            llm=llm,
        )

    async def analyze(
        self,
        question: str,
        session_id: str = "default",
        service: str | None = None,
    ) -> ChatResponse:
        return await self.graph.run(
            question=question,
            session_id=session_id,
            service=service,
        )

    def _persist_analysis(
        self,
        question: str,
        session_id: str,
        service: str,
        response: ChatResponse,
    ) -> None:
        if self.incident_store is None:
            return

        incident = self.incident_store.create_incident(
            title=question[:120],
            service=service,
            question=question,
            session_id=session_id,
        )
        response.metadata["incident_id"] = incident.incident_id
        diagnosis = self.incident_store.save_diagnosis(
            incident_id=incident.incident_id,
            response=response,
        )
        response.metadata["diagnosis_id"] = diagnosis.diagnosis_id

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
        commits = result_map.get("query_recent_commits")
        topology = result_map.get("query_service_topology")

        evidence = self._collect_evidence(metrics, logs, deployments, commits, topology)
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
        commits: ToolResult | None,
        topology: ToolResult | None,
    ) -> list[str]:
        evidence: list[str] = []
        if metrics and metrics.success:
            evidence.append(str(metrics.data.get("summary", "指标查询成功。")))
        if logs and logs.success:
            evidence.append(str(logs.data.get("summary", "日志查询成功。")))
            for item in self._log_evidence_items(logs.data.get("logs")):
                evidence.append(f"{item.get('timestamp')} {item.get('level')}: {item.get('message')}")
        if deployments and deployments.success:
            deployments_data = deployments.data.get("deployments", [])
            if deployments_data:
                latest = deployments_data[0]
                evidence.append(
                    f"{latest.get('deployed_at')} 发布 {latest.get('version')}：{latest.get('summary')}"
                )
        if commits and commits.success:
            commits_data = commits.data.get("commits", [])
            if commits_data:
                latest_commit = commits_data[0]
                evidence.append(
                    f"GitHub latest commit {latest_commit.get('sha')}: {latest_commit.get('message')}"
                )
        if topology and topology.success:
            related_alerts = topology.data.get("related_alerts", [])
            if related_alerts:
                evidence.append(f"发现相邻告警：{related_alerts[0].get('title')}")

        return evidence

    def _log_evidence_items(self, logs: object) -> list[dict[str, str]]:
        if isinstance(logs, list):
            return [item for item in logs[:2] if isinstance(item, dict)]

        if not isinstance(logs, dict):
            return []

        results = logs.get("data", {}).get("result", [])
        if not isinstance(results, list):
            return []

        items: list[dict[str, str]] = []
        for stream in results[:2]:
            if not isinstance(stream, dict):
                continue
            labels = stream.get("stream", {})
            values = stream.get("values", [])
            if not isinstance(labels, dict) or not values:
                continue
            timestamp, message = values[0]
            items.append(
                {
                    "timestamp": str(timestamp),
                    "level": str(labels.get("level") or labels.get("severity") or "INFO"),
                    "message": str(message),
                }
            )
        return items

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

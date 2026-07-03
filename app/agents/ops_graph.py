from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.llm_ops_assistant import LLMOpsAssistant
from app.agents.plan_execute import PlanExecuteReplan
from app.agents.react_loop import ReactLoop
from app.schemas import ChatMode, ChatResponse, DiagnosisReport, PlanTrace, ReactStep, ToolCall, ToolResult
from app.tools import ToolRegistry


@dataclass
class OpsGraphState:
    question: str
    session_id: str
    requested_service: str | None = None
    service: str | None = None
    plan_trace: PlanTrace | None = None
    selected_tool_calls: list[ToolCall] = field(default_factory=list)
    tool_selection_metadata: dict[str, Any] = field(default_factory=dict)
    react_steps: list[ReactStep] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    knowledge_response: ChatResponse | None = None
    fallback_report: DiagnosisReport | None = None
    report: DiagnosisReport | None = None
    summary_metadata: dict[str, Any] = field(default_factory=dict)
    response: ChatResponse | None = None
    graph_trace: list[str] = field(default_factory=list)


class OpsGraphWorkflow:
    """Explicit graph-style workflow for ops diagnosis."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        knowledge_agent: KnowledgeAgent,
        react_loop: ReactLoop,
        plan_execute: PlanExecuteReplan,
        llm_ops_assistant: LLMOpsAssistant,
        infer_service: Callable[[str], str],
        build_report: Callable[[str, list[ToolResult], str], DiagnosisReport],
        format_report: Callable[[DiagnosisReport], str],
        persist_analysis: Callable[[str, str, str, ChatResponse], None],
    ) -> None:
        self.tool_registry = tool_registry
        self.knowledge_agent = knowledge_agent
        self.react_loop = react_loop
        self.plan_execute = plan_execute
        self.llm_ops_assistant = llm_ops_assistant
        self.infer_service = infer_service
        self.build_report = build_report
        self.format_report = format_report
        self.persist_analysis = persist_analysis

    async def run(
        self,
        question: str,
        session_id: str = "default",
        service: str | None = None,
    ) -> ChatResponse:
        state = OpsGraphState(
            question=question,
            session_id=session_id,
            requested_service=service,
        )
        nodes = self._nodes()
        state.graph_trace = [name for name, _ in nodes]

        for _, node in nodes:
            await node(state)

        if state.response is None:
            raise RuntimeError("Ops graph completed without a response.")
        return state.response

    def _nodes(self) -> list[tuple[str, Callable[[OpsGraphState], Any]]]:
        return [
            ("infer_service", self._infer_service_node),
            ("plan", self._plan_node),
            ("select_tools", self._select_tools_node),
            ("execute_tools", self._execute_tools_node),
            ("retrieve_runbook", self._retrieve_runbook_node),
            ("build_fallback_report", self._build_fallback_report_node),
            ("summarize_report", self._summarize_report_node),
            ("build_response", self._build_response_node),
            ("persist_incident", self._persist_incident_node),
        ]

    async def _infer_service_node(self, state: OpsGraphState) -> None:
        state.service = state.requested_service or self.infer_service(state.question)

    async def _plan_node(self, state: OpsGraphState) -> None:
        state.plan_trace = await self.plan_execute.run(service=self._service(state))

    async def _select_tools_node(self, state: OpsGraphState) -> None:
        state.selected_tool_calls, state.tool_selection_metadata = await self.llm_ops_assistant.select_tool_calls(
            question=state.question,
            service=self._service(state),
            fallback_tool_calls=self.react_loop.default_tool_calls(self._service(state)),
        )

    async def _execute_tools_node(self, state: OpsGraphState) -> None:
        state.react_steps = await self.react_loop.run(
            question=state.question,
            service=self._service(state),
            tool_calls=state.selected_tool_calls,
        )
        state.tool_results = [
            step.observation
            for step in state.react_steps
            if step.observation is not None
        ]

    async def _retrieve_runbook_node(self, state: OpsGraphState) -> None:
        state.knowledge_response = await self.knowledge_agent.answer(
            question=state.question,
            session_id=state.session_id,
            top_k=2,
            service=self._service(state),
            incident_type="5xx" if "5xx" in state.question.lower() else None,
        )

    async def _build_fallback_report_node(self, state: OpsGraphState) -> None:
        state.fallback_report = self.build_report(
            self._service(state),
            state.tool_results,
            self._knowledge_response(state).answer,
        )

    async def _summarize_report_node(self, state: OpsGraphState) -> None:
        state.report, state.summary_metadata = await self.llm_ops_assistant.summarize(
            question=state.question,
            service=self._service(state),
            tool_results=state.tool_results,
            sources=self._knowledge_response(state).sources,
            fallback_report=self._fallback_report(state),
        )

    async def _build_response_node(self, state: OpsGraphState) -> None:
        plan_trace = state.plan_trace or PlanTrace()
        knowledge_response = self._knowledge_response(state)
        state.response = ChatResponse(
            session_id=state.session_id,
            answer=self.format_report(self._report(state)),
            mode=ChatMode.ops,
            sources=knowledge_response.sources,
            metadata={
                "service": self._service(state),
                "tool_results": [result.model_dump() for result in state.tool_results],
                "react_steps": [step.model_dump() for step in state.react_steps],
                "plan_trace": plan_trace.model_dump(),
                "runbook_retrieved_count": knowledge_response.metadata.get("retrieved_count", 0),
                "tool_connector": self.tool_registry.describe(),
                "llm_tool_selection": state.tool_selection_metadata,
                "llm_summary": state.summary_metadata,
                "graph_trace": state.graph_trace,
            },
        )

    async def _persist_incident_node(self, state: OpsGraphState) -> None:
        if state.response is None:
            return

        self.persist_analysis(
            state.question,
            state.session_id,
            self._service(state),
            state.response,
        )

    def _service(self, state: OpsGraphState) -> str:
        if state.service is None:
            raise RuntimeError("Ops graph state has no service yet.")
        return state.service

    def _knowledge_response(self, state: OpsGraphState) -> ChatResponse:
        if state.knowledge_response is None:
            raise RuntimeError("Ops graph state has no knowledge response yet.")
        return state.knowledge_response

    def _fallback_report(self, state: OpsGraphState) -> DiagnosisReport:
        if state.fallback_report is None:
            raise RuntimeError("Ops graph state has no fallback report yet.")
        return state.fallback_report

    def _report(self, state: OpsGraphState) -> DiagnosisReport:
        if state.report is None:
            raise RuntimeError("Ops graph state has no final report yet.")
        return state.report

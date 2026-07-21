import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from importlib.util import find_spec
from typing import Any
from uuid import uuid4

from app.agents.langgraph_checkpointing import create_langgraph_checkpointer
from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.llm_ops_assistant import LLMOpsAssistant
from app.agents.plan_execute import PlanExecuteReplan
from app.agents.react_loop import ReactLoop
from app.schemas import (
    AlertSeverity,
    ChatMode,
    ChatResponse,
    DiagnosisReport,
    DiagnosisTaskEventType,
    HumanReviewRequestRecord,
    HumanReviewStatus,
    OpsGraphCheckpointRecord,
    PlanTrace,
    ReactStep,
    ToolCall,
    ToolResult,
)
from app.reviews import build_human_review_plan
from app.storage import SQLiteTaskStore
from app.tools import ToolRegistry


class GraphExecutionCancelled(Exception):
    """Raised when a diagnosis graph should stop before the next node."""


class GraphExecutionPaused(Exception):
    """Raised when a diagnosis graph needs an external decision before continuing."""

    def __init__(self, review_ids: list[str], response: ChatResponse | None = None) -> None:
        self.review_ids = review_ids
        self.response = response
        joined_review_ids = ", ".join(review_ids) if review_ids else "unknown"
        super().__init__(f"Ops graph paused for human review: {joined_review_ids}.")


@dataclass
class OpsGraphState:
    question: str
    session_id: str
    thread_id: str | None = None
    run_id: str | None = None
    requested_service: str | None = None
    alert_severity: AlertSeverity | None = None
    alert_labels: dict[str, str] = field(default_factory=dict)
    trigger_metadata: dict[str, Any] = field(default_factory=dict)
    service: str | None = None
    plan_trace: PlanTrace | None = None
    selected_tool_calls: list[ToolCall] = field(default_factory=list)
    tool_selection_metadata: dict[str, Any] = field(default_factory=dict)
    react_steps: list[ReactStep] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    knowledge_response: ChatResponse | None = None
    fallback_report: DiagnosisReport | None = None
    report: DiagnosisReport | None = None
    human_review_requests: list[dict[str, Any]] = field(default_factory=list)
    summary_metadata: dict[str, Any] = field(default_factory=dict)
    response: ChatResponse | None = None
    graph_trace: list[str] = field(default_factory=list)
    graph_runtime: str = "local"
    graph_runtime_reason: str = "default"
    graph_checkpointer: str = "not_used"


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
        persist_analysis: Callable[
            [str, str, str, ChatResponse, AlertSeverity | None, dict[str, str] | None],
            None,
        ],
        graph_runtime: str = "local",
        graph_checkpointer: str = "memory",
        checkpoint_store: SQLiteTaskStore | None = None,
        should_cancel: Callable[[str], bool] | None = None,
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
        self.graph_runtime = graph_runtime
        self.graph_checkpointer = graph_checkpointer
        self.checkpoint_store = checkpoint_store or SQLiteTaskStore.from_settings()
        self.should_cancel = should_cancel

    async def run(
        self,
        question: str,
        session_id: str = "default",
        service: str | None = None,
        alert_severity: AlertSeverity | None = None,
        alert_labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> ChatResponse:
        metadata = dict(trigger_metadata or {})
        resolved_thread_id = str(
            thread_id
            or metadata.get("thread_id")
            or self._default_thread_id(session_id)
        )
        resolved_run_id = str(run_id or metadata.get("run_id") or f"run_{uuid4().hex}")
        metadata.setdefault("thread_id", resolved_thread_id)
        metadata.setdefault("run_id", resolved_run_id)

        state = OpsGraphState(
            question=question,
            session_id=session_id,
            thread_id=resolved_thread_id,
            run_id=resolved_run_id,
            requested_service=service,
            alert_severity=alert_severity,
            alert_labels=alert_labels or {},
            trigger_metadata=metadata,
        )
        nodes = self._nodes()
        state.graph_trace = [name for name, _ in nodes]

        runtime = self.graph_runtime.strip().lower()
        if runtime == "langgraph":
            await self._run_with_langgraph(state, nodes, strict=True)
        elif runtime == "auto":
            await self._run_with_langgraph(state, nodes, strict=False)
        elif runtime == "local":
            await self._run_local(state, nodes, reason="configured_local")
        else:
            raise ValueError(f"Unsupported OPS_GRAPH_RUNTIME: {self.graph_runtime}")

        if state.response is None:
            raise RuntimeError("Ops graph completed without a response.")
        return state.response

    async def resume(
        self,
        checkpoint: OpsGraphCheckpointRecord | None,
        question: str,
        session_id: str,
        service: str | None = None,
        alert_severity: AlertSeverity | None = None,
        alert_labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> ChatResponse:
        metadata = dict(trigger_metadata or {})
        checkpoint_thread_id = checkpoint.thread_id if checkpoint else None
        resolved_thread_id = str(
            thread_id
            or metadata.get("thread_id")
            or checkpoint_thread_id
            or self._default_thread_id(session_id)
        )
        resolved_run_id = str(run_id or metadata.get("run_id") or f"run_{uuid4().hex}")
        metadata.setdefault("thread_id", resolved_thread_id)
        metadata.setdefault("run_id", resolved_run_id)

        nodes = self._nodes()
        state = self._state_from_checkpoint(
            checkpoint=checkpoint,
            question=question,
            session_id=session_id,
            service=service,
            alert_severity=alert_severity,
            alert_labels=alert_labels or {},
            trigger_metadata=metadata,
            thread_id=resolved_thread_id,
            run_id=resolved_run_id,
        )
        remaining_nodes = self._remaining_nodes_after(
            nodes,
            checkpoint.node_name if checkpoint else None,
        )
        state.graph_trace = [name for name, _ in remaining_nodes]
        self._refresh_response_identity(state)

        runtime = self.graph_runtime.strip().lower()
        if runtime == "langgraph":
            await self._run_with_langgraph(state, remaining_nodes, strict=True)
        elif runtime == "auto":
            await self._run_with_langgraph(state, remaining_nodes, strict=False)
        elif runtime == "local":
            await self._run_local(state, remaining_nodes, reason="resume_from_checkpoint")
        else:
            raise ValueError(f"Unsupported OPS_GRAPH_RUNTIME: {self.graph_runtime}")

        self._refresh_response_identity(state)
        if state.response is None:
            raise RuntimeError("Ops graph resume completed without a response.")
        return state.response

    async def _run_local(
        self,
        state: OpsGraphState,
        nodes: list[tuple[str, Callable[[OpsGraphState], Any]]],
        reason: str,
    ) -> None:
        state.graph_runtime = "local"
        state.graph_runtime_reason = reason
        state.graph_checkpointer = "not_used"
        for name, node in nodes:
            await self._run_node(state, name, node)

    async def _run_with_langgraph(
        self,
        state: OpsGraphState,
        nodes: list[tuple[str, Callable[[OpsGraphState], Any]]],
        strict: bool,
    ) -> None:
        if not self._is_langgraph_available():
            if strict:
                raise RuntimeError(
                    "OPS_GRAPH_RUNTIME=langgraph requires the langgraph package. "
                    "Install it with: pip install -r requirements.txt"
                )
            await self._run_local(state, nodes, reason="langgraph_not_installed")
            return

        try:
            await self._invoke_langgraph(state, nodes)
        except (GraphExecutionCancelled, GraphExecutionPaused):
            raise
        except Exception:
            if strict:
                raise
            await self._run_local(state, nodes, reason="langgraph_runtime_failed")

    async def _invoke_langgraph(
        self,
        state: OpsGraphState,
        nodes: list[tuple[str, Callable[[OpsGraphState], Any]]],
    ) -> None:
        state.graph_runtime = "langgraph"
        state.graph_runtime_reason = "configured_langgraph"
        compiled_graph, checkpointer_name = self._compile_langgraph(nodes)
        state.graph_checkpointer = checkpointer_name
        result = await asyncio.to_thread(
            compiled_graph.invoke,
            self._langgraph_input(state),
            self._langgraph_config(state.thread_id, state.run_id),
        )
        self._apply_langgraph_result(result, state)

    async def resume_interrupt(
        self,
        thread_id: str,
        run_id: str | None,
        resume_value: dict[str, Any],
    ) -> ChatResponse:
        nodes = self._nodes()
        compiled_graph, checkpointer_name = self._compile_langgraph(nodes)
        from langgraph.types import Command

        result = await asyncio.to_thread(
            compiled_graph.invoke,
            Command(resume=resume_value),
            self._langgraph_config(thread_id, run_id),
        )
        final_state = self._final_state_from_langgraph_result(result)
        final_state.graph_runtime = "langgraph"
        final_state.graph_runtime_reason = "native_interrupt_resume"
        final_state.graph_checkpointer = checkpointer_name
        self._refresh_response_identity(final_state)
        if final_state.response is None:
            raise RuntimeError("LangGraph interrupt resume completed without a response.")
        return final_state.response

    def _compile_langgraph(
        self,
        nodes: list[tuple[str, Callable[[OpsGraphState], Any]]],
    ) -> tuple[Any, str]:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        for name, node in nodes:
            graph.add_node(name, self._langgraph_node(name, node))

        graph.set_entry_point(nodes[0][0])
        for index in range(len(nodes) - 1):
            graph.add_edge(nodes[index][0], nodes[index + 1][0])
        graph.add_edge(nodes[-1][0], END)

        checkpointer, checkpointer_name = create_langgraph_checkpointer(self.graph_checkpointer)
        return graph.compile(checkpointer=checkpointer), checkpointer_name

    def _langgraph_config(self, thread_id: str | None, run_id: str | None) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "metadata": {
                "thread_id": thread_id,
                "run_id": run_id,
            },
        }

    def _langgraph_input(self, state: OpsGraphState) -> dict[str, Any]:
        return {"ops_state": self._state_snapshot(state)}

    def _apply_langgraph_result(
        self,
        result: dict[str, Any],
        state: OpsGraphState,
    ) -> None:
        final_state = self._final_state_from_langgraph_result(result)
        state.__dict__.update(final_state.__dict__)
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            review_ids = self._review_ids_from_interrupts(interrupts)
            error = "LangGraph interrupted for human review."
            self._refresh_response_identity(state)
            self._save_checkpoint(state, "human_review_gate", "paused", error=error)
            raise GraphExecutionPaused(review_ids=review_ids, response=state.response)

    def _final_state_from_langgraph_result(self, result: dict[str, Any]) -> OpsGraphState:
        final_state = result.get("ops_state")
        if isinstance(final_state, OpsGraphState):
            return final_state
        if isinstance(final_state, dict):
            return self._state_from_snapshot(final_state)
        raise RuntimeError("LangGraph did not return an OpsGraphState.")

    def _review_ids_from_interrupts(self, interrupts: list[Any]) -> list[str]:
        review_ids: list[str] = []
        for item in interrupts:
            value = getattr(item, "value", item)
            if not isinstance(value, dict):
                continue
            for review_id in value.get("review_ids", []):
                review_ids.append(str(review_id))
        return review_ids

    def _langgraph_node(
        self,
        name: str,
        node: Callable[[OpsGraphState], Any],
    ) -> Callable[[dict[str, Any]], Any]:
        def wrapped(raw_state: dict[str, Any]) -> dict[str, Any]:
            ops_state = self._state_from_langgraph_payload(raw_state.get("ops_state"))
            if self._should_use_native_human_review_interrupt(ops_state, name):
                self._run_native_human_review_gate_node(ops_state, name, raw_state)
            else:
                asyncio.run(self._run_node(ops_state, name, node))
                raw_state["ops_state"] = self._state_snapshot(ops_state)
            return {"ops_state": raw_state["ops_state"]}

        return wrapped

    def _state_from_langgraph_payload(self, value: Any) -> OpsGraphState:
        if isinstance(value, OpsGraphState):
            return value
        if isinstance(value, dict):
            return self._state_from_snapshot(value)
        raise RuntimeError("LangGraph node state did not contain a valid ops_state.")

    def _is_langgraph_available(self) -> bool:
        return find_spec("langgraph") is not None

    async def _run_node(
        self,
        state: OpsGraphState,
        name: str,
        node: Callable[[OpsGraphState], Any],
    ) -> None:
        self._raise_if_cancel_requested(state, name)
        self._refresh_response_identity(state)
        self._save_checkpoint(state, name, "started")
        try:
            await node(state)
        except GraphExecutionPaused as exc:
            self._refresh_response_identity(state)
            self._save_checkpoint(state, name, "paused", error=str(exc))
            raise
        except Exception as exc:
            self._save_checkpoint(state, name, "failed", error=str(exc))
            raise
        self._refresh_response_identity(state)
        self._save_checkpoint(state, name, "completed")

    def _raise_if_cancel_requested(self, state: OpsGraphState, node_name: str) -> None:
        task_id = self._task_id(state)
        if not task_id or self.should_cancel is None or not self.should_cancel(task_id):
            return

        self._save_checkpoint(state, node_name, "canceled")
        raise GraphExecutionCancelled(f"Task {task_id} was canceled before node {node_name}.")

    def _save_checkpoint(
        self,
        state: OpsGraphState,
        node_name: str,
        status: str,
        error: str | None = None,
    ) -> None:
        task_id = self._task_id(state)
        if not task_id:
            return

        self.checkpoint_store.save_graph_checkpoint(
            task_id=task_id,
            node_name=node_name,
            status=status,
            state=self._state_snapshot(state),
            error=error,
            thread_id=state.thread_id,
            run_id=state.run_id,
        )
        self._append_graph_event(task_id, node_name, status, error)

    def _append_graph_event(
        self,
        task_id: str,
        node_name: str,
        status: str,
        error: str | None,
    ) -> None:
        event_map = {
            "started": DiagnosisTaskEventType.graph_node_started,
            "completed": DiagnosisTaskEventType.graph_node_completed,
            "paused": DiagnosisTaskEventType.graph_node_paused,
            "canceled": DiagnosisTaskEventType.graph_node_canceled,
            "failed": DiagnosisTaskEventType.graph_node_failed,
        }
        event_type = event_map.get(status)
        if event_type is None:
            return

        self.checkpoint_store.append_event(
            task_id=task_id,
            event_type=event_type,
            message=f"Ops graph node {node_name} {status}.",
            data={
                "node_name": node_name,
                "status": status,
                "error": error,
            },
        )

    def _task_id(self, state: OpsGraphState) -> str | None:
        raw_task_id = state.trigger_metadata.get("task_id")
        return str(raw_task_id) if raw_task_id else None

    def _state_snapshot(self, state: OpsGraphState) -> dict[str, Any]:
        return {
            "question": state.question,
            "session_id": state.session_id,
            "thread_id": state.thread_id,
            "run_id": state.run_id,
            "requested_service": state.requested_service,
            "service": state.service,
            "alert_severity": state.alert_severity.value if state.alert_severity else None,
            "alert_labels": _json_safe(state.alert_labels),
            "trigger_metadata": _json_safe(state.trigger_metadata),
            "plan_trace": _dump_model(state.plan_trace) if state.plan_trace else None,
            "selected_tool_calls": [_dump_model(call) for call in state.selected_tool_calls],
            "tool_selection_metadata": _json_safe(state.tool_selection_metadata),
            "react_steps": [_dump_model(step) for step in state.react_steps],
            "tool_results": [_dump_model(result) for result in state.tool_results],
            "knowledge_response": _dump_model(state.knowledge_response)
            if state.knowledge_response
            else None,
            "fallback_report": _dump_model(state.fallback_report)
            if state.fallback_report
            else None,
            "report": _dump_model(state.report) if state.report else None,
            "response": _dump_model(state.response) if state.response else None,
            "source_doc_ids": [
                source.doc_id
                for source in state.knowledge_response.sources
            ]
            if state.knowledge_response
            else [],
            "runbook_retrieved_count": (
                state.knowledge_response.metadata.get("retrieved_count", 0)
                if state.knowledge_response
                else 0
            ),
            "has_plan_trace": state.plan_trace is not None,
            "has_fallback_report": state.fallback_report is not None,
            "has_report": state.report is not None,
            "human_review_requests": _json_safe(state.human_review_requests),
            "has_response": state.response is not None,
            "summary_metadata": _json_safe(state.summary_metadata),
            "graph_runtime": state.graph_runtime,
            "graph_runtime_reason": state.graph_runtime_reason,
            "graph_checkpointer": state.graph_checkpointer,
            "graph_trace": state.graph_trace,
        }

    def _state_from_checkpoint(
        self,
        checkpoint: OpsGraphCheckpointRecord | None,
        question: str,
        session_id: str,
        service: str | None,
        alert_severity: AlertSeverity | None,
        alert_labels: dict[str, str],
        trigger_metadata: dict[str, Any],
        thread_id: str,
        run_id: str,
    ) -> OpsGraphState:
        snapshot = checkpoint.state if checkpoint else {}
        state = self._state_from_snapshot(
            snapshot=snapshot,
            question=question,
            session_id=session_id,
            service=service,
            alert_severity=alert_severity,
            alert_labels=alert_labels,
            trigger_metadata=trigger_metadata,
            thread_id=thread_id,
            run_id=run_id,
        )
        state.thread_id = thread_id
        state.run_id = run_id
        state.trigger_metadata = trigger_metadata
        return state

    def _state_from_snapshot(
        self,
        snapshot: dict[str, Any],
        question: str | None = None,
        session_id: str | None = None,
        service: str | None = None,
        alert_severity: AlertSeverity | None = None,
        alert_labels: dict[str, str] | None = None,
        trigger_metadata: dict[str, Any] | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> OpsGraphState:
        return OpsGraphState(
            question=str(snapshot.get("question") or question or ""),
            session_id=str(snapshot.get("session_id") or session_id or "default"),
            thread_id=str(snapshot.get("thread_id") or thread_id)
            if snapshot.get("thread_id") or thread_id
            else None,
            run_id=str(snapshot.get("run_id") or run_id)
            if snapshot.get("run_id") or run_id
            else None,
            requested_service=snapshot.get("requested_service") or service,
            alert_severity=_alert_severity(snapshot.get("alert_severity"), alert_severity),
            alert_labels=snapshot.get("alert_labels") or alert_labels or {},
            trigger_metadata=trigger_metadata
            if trigger_metadata is not None
            else snapshot.get("trigger_metadata") or {},
            service=snapshot.get("service") or service,
            plan_trace=_optional_model(snapshot.get("plan_trace"), PlanTrace),
            selected_tool_calls=_model_list(snapshot.get("selected_tool_calls"), ToolCall),
            tool_selection_metadata=snapshot.get("tool_selection_metadata") or {},
            react_steps=_model_list(snapshot.get("react_steps"), ReactStep),
            tool_results=_model_list(snapshot.get("tool_results"), ToolResult),
            knowledge_response=_optional_model(snapshot.get("knowledge_response"), ChatResponse),
            fallback_report=_optional_model(snapshot.get("fallback_report"), DiagnosisReport),
            report=_optional_model(snapshot.get("report"), DiagnosisReport),
            human_review_requests=snapshot.get("human_review_requests") or [],
            summary_metadata=snapshot.get("summary_metadata") or {},
            response=_optional_model(snapshot.get("response"), ChatResponse),
            graph_runtime=snapshot.get("graph_runtime") or "local",
            graph_runtime_reason=snapshot.get("graph_runtime_reason") or "restored",
            graph_checkpointer=snapshot.get("graph_checkpointer") or "not_used",
            graph_trace=snapshot.get("graph_trace") or [],
        )

    def _remaining_nodes_after(
        self,
        nodes: list[tuple[str, Callable[[OpsGraphState], Any]]],
        node_name: str | None,
    ) -> list[tuple[str, Callable[[OpsGraphState], Any]]]:
        if node_name is None:
            return nodes

        node_names = [name for name, _ in nodes]
        if node_name not in node_names:
            raise ValueError(f"Checkpoint node is not in the ops graph: {node_name}")

        next_index = node_names.index(node_name) + 1
        if next_index >= len(nodes):
            raise ValueError("No remaining graph nodes to resume.")
        return nodes[next_index:]

    def _refresh_response_identity(self, state: OpsGraphState) -> None:
        if state.response is None:
            return

        state.response.session_id = state.session_id
        state.response.metadata["trigger"] = state.trigger_metadata
        state.response.metadata["graph_run"] = {
            "thread_id": state.thread_id,
            "run_id": state.run_id,
        }
        state.response.metadata["graph_trace"] = state.graph_trace
        state.response.metadata["graph_runtime"] = {
            "requested": self.graph_runtime,
            "used": state.graph_runtime,
            "reason": state.graph_runtime_reason,
            "checkpointer_requested": self.graph_checkpointer,
            "checkpointer_used": state.graph_checkpointer,
            "langgraph_available": self._is_langgraph_available(),
        }

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
            ("human_review_gate", self._human_review_gate_node),
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
                "trigger": state.trigger_metadata,
                "graph_run": {
                    "thread_id": state.thread_id,
                    "run_id": state.run_id,
                },
                "graph_trace": state.graph_trace,
                "graph_runtime": {
                    "requested": self.graph_runtime,
                    "used": state.graph_runtime,
                    "reason": state.graph_runtime_reason,
                    "checkpointer_requested": self.graph_checkpointer,
                    "checkpointer_used": state.graph_checkpointer,
                    "langgraph_available": self._is_langgraph_available(),
                },
            },
        )

    async def _human_review_gate_node(self, state: OpsGraphState) -> None:
        payload = self._prepare_human_review_gate(state)
        if payload is None:
            return
        raise GraphExecutionPaused(
            review_ids=[str(review_id) for review_id in payload["review_ids"]],
            response=state.response,
        )

    def _should_use_native_human_review_interrupt(
        self,
        state: OpsGraphState,
        node_name: str,
    ) -> bool:
        return (
            node_name == "human_review_gate"
            and state.graph_runtime == "langgraph"
            and state.graph_checkpointer not in {"disabled", "not_used"}
        )

    def _run_native_human_review_gate_node(
        self,
        state: OpsGraphState,
        node_name: str,
        raw_state: dict[str, Any],
    ) -> None:
        from langgraph.types import interrupt

        self._raise_if_cancel_requested(state, node_name)
        self._refresh_response_identity(state)
        self._save_checkpoint(state, node_name, "started")
        payload = self._prepare_human_review_gate(state)
        if payload is None:
            self._refresh_response_identity(state)
            self._save_checkpoint(state, node_name, "completed")
            raw_state["ops_state"] = self._state_snapshot(state)
            return

        raw_state["ops_state"] = self._state_snapshot(state)
        resume_value = interrupt(payload)
        self._apply_human_review_resume(state, resume_value)
        self._refresh_response_identity(state)
        self._save_checkpoint(state, node_name, "completed")
        raw_state["ops_state"] = self._state_snapshot(state)

    def _prepare_human_review_gate(self, state: OpsGraphState) -> dict[str, Any] | None:
        if state.response is None:
            raise RuntimeError("Ops graph state has no response before human review gate.")

        review_plan = build_human_review_plan(self._report(state))
        if not review_plan.required:
            state.response.metadata["human_review"] = {
                "required": False,
                "requests": [],
            }
            return

        task_id = self._task_id(state)
        if not task_id:
            state.response.metadata["human_review"] = {
                "required": True,
                "status": "not_persisted",
                "reason": "missing_task_id",
                "proposed_actions": review_plan.proposed_actions,
                "risk_reasons": review_plan.risk_reasons,
                "requests": [],
            }
            return

        review, created = self._get_or_create_human_review_request(
            state=state,
            task_id=task_id,
            proposed_actions=review_plan.proposed_actions,
            risk_reasons=review_plan.risk_reasons,
        )
        reviews = self._human_reviews_for_current_run(state, task_id)
        if review.review_id not in {item.review_id for item in reviews}:
            reviews.append(review)

        state.human_review_requests = [
            item.model_dump(mode="json")
            for item in reviews
        ]
        status = self._human_review_status(reviews)
        state.response.metadata["human_review"] = {
            "required": True,
            "status": status.value,
            "review_ids": [item.review_id for item in reviews],
            "requests": state.human_review_requests,
        }
        if created:
            self.checkpoint_store.append_event(
                task_id=task_id,
                event_type=DiagnosisTaskEventType.human_review_requested,
                message="Human review requested for high-risk proposed actions.",
                data={
                    "review_id": review.review_id,
                    "proposed_actions": review.proposed_actions,
                    "risk_reasons": review.risk_reasons,
                },
            )

        return {
            "kind": "human_review",
            "task_id": task_id,
            "service": self._service(state),
            "thread_id": state.thread_id,
            "run_id": state.run_id,
            "review_ids": [item.review_id for item in reviews],
            "status": status.value,
            "proposed_actions": review_plan.proposed_actions,
            "risk_reasons": review_plan.risk_reasons,
        }

    def _get_or_create_human_review_request(
        self,
        state: OpsGraphState,
        task_id: str,
        proposed_actions: list[str],
        risk_reasons: list[str],
    ) -> tuple[HumanReviewRequestRecord, bool]:
        reviews = self._human_reviews_for_current_run(state, task_id)
        if reviews:
            return reviews[-1], False

        review = self.checkpoint_store.create_human_review_request(
            task_id=task_id,
            service=self._service(state),
            proposed_actions=proposed_actions,
            risk_reasons=risk_reasons,
            metadata={
                "session_id": state.session_id,
                "thread_id": state.thread_id,
                "run_id": state.run_id,
                "trigger": state.trigger_metadata,
                "graph_runtime": state.graph_runtime,
                "graph_runtime_reason": state.graph_runtime_reason,
                "gate": "langgraph_interrupt"
                if state.graph_runtime == "langgraph"
                else "local_pause",
            },
        )
        return review, True

    def _human_reviews_for_current_run(
        self,
        state: OpsGraphState,
        task_id: str,
    ) -> list[HumanReviewRequestRecord]:
        return [
            review
            for review in self.checkpoint_store.list_human_review_requests_for_task(task_id)
            if review.metadata.get("run_id") == state.run_id
        ]

    def _human_review_status(
        self,
        reviews: list[HumanReviewRequestRecord],
    ) -> HumanReviewStatus:
        if any(review.status == HumanReviewStatus.pending for review in reviews):
            return HumanReviewStatus.pending
        if any(review.status == HumanReviewStatus.rejected for review in reviews):
            return HumanReviewStatus.rejected
        return HumanReviewStatus.approved

    def _apply_human_review_resume(
        self,
        state: OpsGraphState,
        resume_value: Any,
    ) -> None:
        task_id = self._task_id(state)
        if not task_id or state.response is None:
            return

        reviews = self._human_reviews_for_current_run(state, task_id)
        if not reviews:
            return

        state.human_review_requests = [
            review.model_dump(mode="json")
            for review in reviews
        ]
        state.response.metadata["human_review"] = {
            "required": True,
            "status": self._human_review_status(reviews).value,
            "review_ids": [review.review_id for review in reviews],
            "requests": state.human_review_requests,
            "resume": resume_value if isinstance(resume_value, dict) else {"value": resume_value},
        }

    async def _persist_incident_node(self, state: OpsGraphState) -> None:
        if state.response is None:
            return

        self.persist_analysis(
            state.question,
            state.session_id,
            self._service(state),
            state.response,
            state.alert_severity,
            state.alert_labels,
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

    def _default_thread_id(self, session_id: str) -> str:
        safe_session_id = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in session_id
        )
        return f"thread_session_{safe_session_id[:80] or 'default'}"


def _dump_model(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))

    return str(value)


def _optional_model(value: Any, model_type: type[Any]) -> Any | None:
    if value is None:
        return None
    return model_type.model_validate(value)


def _model_list(values: Any, model_type: type[Any]) -> list[Any]:
    if not values:
        return []
    if not isinstance(values, list):
        raise ValueError("Checkpoint state expected a list.")
    return [model_type.model_validate(value) for value in values]


def _alert_severity(value: Any, fallback: AlertSeverity | None) -> AlertSeverity | None:
    if value is None:
        return fallback
    if isinstance(value, AlertSeverity):
        return value
    return AlertSeverity(str(value))

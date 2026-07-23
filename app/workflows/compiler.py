from __future__ import annotations

import asyncio
import operator
from time import perf_counter
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict
from uuid import uuid4

from app.agents.langgraph_checkpointing import create_langgraph_checkpointer
from app.config import settings
from app.schemas import (
    WorkflowDraftRunResponse,
    WorkflowGraphDefinition,
    WorkflowRunEventType,
    WorkflowRunStatus,
    WorkflowValidationReport,
)
from app.workflows.runtime import WorkflowNodeRuntime
from app.workflows.validator import WorkflowValidator


class WorkflowRuntimeState(TypedDict, total=False):
    inputs: dict[str, Any]
    node_outputs: Annotated[dict[str, Any], _merge_dicts]
    trace: Annotated[list[str], operator.add]
    output: Any


class WorkflowValidationError(ValueError):
    def __init__(self, report: WorkflowValidationReport) -> None:
        self.report = report
        codes = ", ".join(issue.code for issue in report.issues) or "unknown"
        super().__init__(f"Workflow validation failed: {codes}")


WorkflowEventHandler = Callable[
    [WorkflowRunEventType, str | None, str, dict[str, Any]],
    None,
]
_NO_RESUME = object()


@dataclass
class CompiledWorkflow:
    graph: Any
    definition: WorkflowGraphDefinition
    report: WorkflowValidationReport
    checkpointer_name: str

    async def run(
        self,
        app_id: str,
        draft_revision: int,
        inputs: dict[str, Any],
        thread_id: str | None = None,
        run_id: str | None = None,
        resume_value: Any = _NO_RESUME,
    ) -> WorkflowDraftRunResponse:
        resolved_thread_id = thread_id or f"wfthread_{uuid4().hex}"
        resolved_run_id = run_id or f"wfrun_{uuid4().hex}"
        if resume_value is _NO_RESUME:
            graph_input: Any = {
                "inputs": _prepare_inputs(self.definition.variables, inputs),
                "node_outputs": {},
                "trace": [],
            }
        else:
            from langgraph.types import Command

            graph_input = Command(resume=resume_value)
        result = await asyncio.to_thread(
            self.graph.invoke,
            graph_input,
            {
                "configurable": {
                    "thread_id": resolved_thread_id,
                    "checkpoint_ns": resolved_run_id,
                }
            },
        )
        interrupts = result.get("__interrupt__") or []
        review_requests = [
            value
            for value in (_interrupt_value(item) for item in interrupts)
            if isinstance(value, dict)
        ]
        return WorkflowDraftRunResponse(
            app_id=app_id,
            draft_revision=draft_revision,
            thread_id=resolved_thread_id,
            run_id=resolved_run_id,
            status=(
                WorkflowRunStatus.waiting_review
                if review_requests
                else WorkflowRunStatus.succeeded
            ),
            output=result.get("output"),
            node_outputs=result.get("node_outputs", {}),
            trace=result.get("trace", []),
            review_requests=review_requests,
            metadata={
                "runtime": "langgraph",
                "schema_version": self.definition.schema_version,
                "node_count": self.report.node_count,
                "edge_count": self.report.edge_count,
                "checkpointer": self.checkpointer_name,
            },
        )


class WorkflowCompiler:
    """Compile a validated workflow definition into a native LangGraph graph."""

    def __init__(
        self,
        validator: WorkflowValidator | None = None,
        runtime: WorkflowNodeRuntime | None = None,
        checkpointer: Any | None = None,
        checkpointer_name: str | None = None,
    ) -> None:
        self.validator = validator or WorkflowValidator()
        self.runtime = runtime or WorkflowNodeRuntime()
        if checkpointer_name is None:
            self.checkpointer, self.checkpointer_name = create_langgraph_checkpointer(
                settings.workflow_checkpointer,
                settings.workflow_checkpoint_db_path,
                setting_name="WORKFLOW_CHECKPOINTER",
            )
        else:
            self.checkpointer = checkpointer
            self.checkpointer_name = checkpointer_name

    def compile(
        self,
        definition: WorkflowGraphDefinition,
        event_handler: WorkflowEventHandler | None = None,
    ) -> CompiledWorkflow:
        report = self.validator.validate(definition)
        if not report.valid:
            raise WorkflowValidationError(report)

        from langgraph.graph import END, StateGraph

        incoming: dict[str, list[str]] = {
            node.node_id: [] for node in definition.nodes
        }
        for edge in definition.edges:
            incoming[edge.target_node_id].append(edge.source_node_id)

        graph = StateGraph(WorkflowRuntimeState)
        for node in definition.nodes:
            graph.add_node(
                node.node_id,
                self._node_handler(
                    node,
                    incoming[node.node_id],
                    event_handler=event_handler,
                ),
            )
        graph.set_entry_point(str(report.start_node_id))
        for edge in definition.edges:
            graph.add_edge(edge.source_node_id, edge.target_node_id)
        graph.add_edge(str(report.end_node_id), END)
        return CompiledWorkflow(
            graph=graph.compile(checkpointer=self.checkpointer),
            definition=definition,
            report=report,
            checkpointer_name=self.checkpointer_name,
        )

    def _node_handler(
        self,
        node,
        predecessor_ids: list[str],
        event_handler: WorkflowEventHandler | None = None,
    ):
        def execute(state: WorkflowRuntimeState) -> dict[str, Any]:
            started_at = perf_counter()
            _emit_event(
                event_handler,
                WorkflowRunEventType.node_started,
                node.node_id,
                f"Workflow node '{node.name}' started.",
                {"node_type": node.node_type.value},
            )
            try:
                result = asyncio.run(
                    self.runtime.execute(node, dict(state), predecessor_ids)
                )
            except Exception as exc:
                from langgraph.errors import GraphInterrupt

                elapsed_ms = int((perf_counter() - started_at) * 1000)
                if isinstance(exc, GraphInterrupt):
                    _emit_event(
                        event_handler,
                        WorkflowRunEventType.node_paused,
                        node.node_id,
                        f"Workflow node '{node.name}' paused for human review.",
                        {"node_type": node.node_type.value, "elapsed_ms": elapsed_ms},
                    )
                    raise
                _emit_event(
                    event_handler,
                    WorkflowRunEventType.node_failed,
                    node.node_id,
                    f"Workflow node '{node.name}' failed.",
                    {
                        "node_type": node.node_type.value,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc)[:4000],
                    },
                )
                raise
            update: dict[str, Any] = {"trace": [node.node_id]}
            if node.node_type.value == "end":
                update["output"] = result
            else:
                update["node_outputs"] = {node.node_id: result}
            _emit_event(
                event_handler,
                WorkflowRunEventType.node_completed,
                node.node_id,
                f"Workflow node '{node.name}' completed.",
                {
                    "node_type": node.node_type.value,
                    "elapsed_ms": int((perf_counter() - started_at) * 1000),
                },
            )
            return update

        return execute


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


def _interrupt_value(item: Any) -> Any:
    value = getattr(item, "value", item)
    interrupt_id = getattr(item, "id", None)
    if isinstance(value, dict) and interrupt_id:
        return {**value, "interrupt_id": interrupt_id}
    return value


def _prepare_inputs(
    variable_definitions: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    resolved = dict(inputs)
    for name, definition in variable_definitions.items():
        if name not in resolved and "default" in definition:
            resolved[name] = definition["default"]
        if definition.get("required", False) and name not in resolved:
            raise ValueError(f"Required workflow input is missing: {name}")
        if name in resolved and not _matches_variable_type(
            resolved[name],
            str(definition["type"]),
        ):
            raise ValueError(
                f"Workflow input '{name}' must have type {definition['type']}."
            )
    return resolved


def _matches_variable_type(value: Any, variable_type: str) -> bool:
    if variable_type == "string":
        return isinstance(value, str)
    if variable_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if variable_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if variable_type == "boolean":
        return isinstance(value, bool)
    if variable_type == "object":
        return isinstance(value, dict)
    if variable_type == "array":
        return isinstance(value, list)
    return False


def _emit_event(
    handler: WorkflowEventHandler | None,
    event_type: WorkflowRunEventType,
    node_id: str | None,
    message: str,
    data: dict[str, Any],
) -> None:
    if handler is not None:
        handler(event_type, node_id, message, data)

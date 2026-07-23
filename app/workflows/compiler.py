from __future__ import annotations

import asyncio
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from app.schemas import (
    WorkflowDraftRunResponse,
    WorkflowGraphDefinition,
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


@dataclass
class CompiledWorkflow:
    graph: Any
    definition: WorkflowGraphDefinition
    report: WorkflowValidationReport

    async def run(
        self,
        app_id: str,
        draft_revision: int,
        inputs: dict[str, Any],
        thread_id: str | None = None,
    ) -> WorkflowDraftRunResponse:
        resolved_thread_id = thread_id or f"wfthread_{uuid4().hex}"
        resolved_inputs = _prepare_inputs(self.definition.variables, inputs)
        result = await asyncio.to_thread(
            self.graph.invoke,
            {
                "inputs": resolved_inputs,
                "node_outputs": {},
                "trace": [],
            },
            {"configurable": {"thread_id": resolved_thread_id}},
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
            },
        )


class WorkflowCompiler:
    """Compile a validated workflow definition into a native LangGraph graph."""

    def __init__(
        self,
        validator: WorkflowValidator | None = None,
        runtime: WorkflowNodeRuntime | None = None,
    ) -> None:
        self.validator = validator or WorkflowValidator()
        self.runtime = runtime or WorkflowNodeRuntime()

    def compile(self, definition: WorkflowGraphDefinition) -> CompiledWorkflow:
        report = self.validator.validate(definition)
        if not report.valid:
            raise WorkflowValidationError(report)

        from langgraph.checkpoint.memory import InMemorySaver
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
                self._node_handler(node, incoming[node.node_id]),
            )
        graph.set_entry_point(str(report.start_node_id))
        for edge in definition.edges:
            graph.add_edge(edge.source_node_id, edge.target_node_id)
        graph.add_edge(str(report.end_node_id), END)
        return CompiledWorkflow(
            graph=graph.compile(checkpointer=InMemorySaver()),
            definition=definition,
            report=report,
        )

    def _node_handler(self, node, predecessor_ids: list[str]):
        def execute(state: WorkflowRuntimeState) -> dict[str, Any]:
            result = asyncio.run(
                self.runtime.execute(node, dict(state), predecessor_ids)
            )
            update: dict[str, Any] = {"trace": [node.node_id]}
            if node.node_type.value == "end":
                update["output"] = result
            else:
                update["node_outputs"] = {node.node_id: result}
            return update

        return execute


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


def _interrupt_value(item: Any) -> Any:
    return getattr(item, "value", item)


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

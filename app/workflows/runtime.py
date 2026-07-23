from __future__ import annotations

import re
from typing import Any

from app.agents.knowledge_agent import KnowledgeAgent
from app.config import settings
from app.llm import LLMClient, create_llm_client
from app.rag.access_control import system_access_context
from app.schemas import (
    ChatMessage,
    MessageRole,
    ToolCall,
    WorkflowNodeDefinition,
    WorkflowNodeType,
)
from app.tools import ToolRegistry, create_ops_tool_registry

_TEMPLATE_PATTERN = re.compile(r"\$\{([^{}]+)\}")


class WorkflowNodeExecutionError(RuntimeError):
    def __init__(self, node_id: str, message: str) -> None:
        self.node_id = node_id
        super().__init__(f"Workflow node '{node_id}' failed: {message}")


class WorkflowNodeRuntime:
    """Execute validated workflow node contracts against existing project services."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        knowledge_agent: KnowledgeAgent | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._knowledge_agent = knowledge_agent
        self._llm = llm

    async def execute(
        self,
        node: WorkflowNodeDefinition,
        state: dict[str, Any],
        predecessor_ids: list[str],
    ) -> Any:
        context = {
            "inputs": state.get("inputs", {}),
            "node_outputs": state.get("node_outputs", {}),
        }
        if node.node_type == WorkflowNodeType.start:
            return {"inputs": state.get("inputs", {})}
        if node.node_type == WorkflowNodeType.end:
            return self._end_output(node, state, predecessor_ids, context)
        if node.node_type == WorkflowNodeType.tool:
            return await self._execute_tool(node, context)
        if node.node_type == WorkflowNodeType.knowledge_retrieval:
            return self._execute_knowledge(node, context)
        if node.node_type == WorkflowNodeType.agent:
            return await self._execute_agent(node, context)
        if node.node_type == WorkflowNodeType.human_review:
            return self._interrupt_for_review(node, context)
        raise WorkflowNodeExecutionError(node.node_id, f"Unsupported node type: {node.node_type}")

    async def _execute_tool(
        self,
        node: WorkflowNodeDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name = str(node.config["tool_name"])
        arguments = resolve_templates(node.config.get("arguments", {}), context)
        result = await self.tool_registry.execute(
            ToolCall(name=tool_name, arguments=arguments)
        )
        if not result.success and node.config.get("fail_on_error", True):
            raise WorkflowNodeExecutionError(
                node.node_id,
                result.error or f"Tool '{tool_name}' returned an error.",
            )
        return result.model_dump(mode="json")

    def _execute_knowledge(
        self,
        node: WorkflowNodeDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        query_template = node.config.get("query", "${inputs.question}")
        query = resolve_templates(query_template, context)
        if not isinstance(query, str) or not query.strip():
            raise WorkflowNodeExecutionError(node.node_id, "Knowledge query resolved to an empty value.")
        service = resolve_templates(node.config.get("service"), context)
        incident_type = resolve_templates(node.config.get("incident_type"), context)
        results = self.knowledge_agent.search(
            question=query,
            top_k=int(node.config.get("top_k", 3)),
            service=str(service) if service else None,
            incident_type=str(incident_type) if incident_type else None,
            access_context=system_access_context(),
        )
        return {
            "query": query,
            "results": [result.model_dump(mode="json") for result in results],
        }

    async def _execute_agent(
        self,
        node: WorkflowNodeDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = resolve_templates(node.config["prompt"], context)
        system_prompt = resolve_templates(
            node.config.get(
                "system_prompt",
                "You are an enterprise OnCall workflow agent. Return a concise operational answer.",
            ),
            context,
        )
        answer = await self.llm.generate(
            [
                ChatMessage(role=MessageRole.system, content=str(system_prompt)),
                ChatMessage(role=MessageRole.user, content=str(prompt)),
            ]
        )
        return {"answer": answer}

    def _interrupt_for_review(
        self,
        node: WorkflowNodeDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        from langgraph.types import interrupt

        payload = {
            "node_id": node.node_id,
            "title": str(node.config.get("title") or node.name),
            "message": resolve_templates(
                node.config.get("message", "Workflow requires human approval."),
                context,
            ),
            "context": context,
        }
        decision = interrupt(payload)
        return {"review": payload, "decision": decision}

    def _end_output(
        self,
        node: WorkflowNodeDefinition,
        state: dict[str, Any],
        predecessor_ids: list[str],
        context: dict[str, Any],
    ) -> Any:
        if "output" in node.config:
            return resolve_templates(node.config["output"], context)
        node_outputs = state.get("node_outputs", {})
        if len(predecessor_ids) == 1:
            return node_outputs.get(predecessor_ids[0])
        return node_outputs

    @property
    def tool_registry(self) -> ToolRegistry:
        if self._tool_registry is None:
            self._tool_registry = create_ops_tool_registry()
        return self._tool_registry

    @property
    def knowledge_agent(self) -> KnowledgeAgent:
        if self._knowledge_agent is None:
            self._knowledge_agent = KnowledgeAgent.from_runbook_directory(
                settings.knowledge_local_path
            )
        return self._knowledge_agent

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm_client()
        return self._llm


def resolve_templates(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_templates(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, context) for item in value]
    if not isinstance(value, str):
        return value

    exact_match = _TEMPLATE_PATTERN.fullmatch(value)
    if exact_match:
        return _resolve_path(exact_match.group(1), context)

    def replace(match: re.Match[str]) -> str:
        return str(_resolve_path(match.group(1), context))

    return _TEMPLATE_PATTERN.sub(replace, value)


def _resolve_path(path: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ValueError(f"Workflow template path not found: {path}")
        current = current[segment]
    return current

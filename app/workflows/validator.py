from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any

from app.schemas import (
    WorkflowGraphDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeType,
    WorkflowValidationIssue,
    WorkflowValidationReport,
)


class WorkflowValidator:
    """Validate graph safety and executable node contracts before compilation."""

    def validate(self, graph: WorkflowGraphDefinition) -> WorkflowValidationReport:
        issues: list[WorkflowValidationIssue] = []
        node_counts = Counter(node.node_id for node in graph.nodes)
        edge_counts = Counter(edge.edge_id for edge in graph.edges)
        for node_id, count in node_counts.items():
            if count > 1:
                issues.append(_node_issue("duplicate_node_id", f"Node ID '{node_id}' is duplicated.", node_id))
        for edge_id, count in edge_counts.items():
            if count > 1:
                issues.append(_edge_issue("duplicate_edge_id", f"Edge ID '{edge_id}' is duplicated.", edge_id))

        unique_nodes = {node.node_id: node for node in graph.nodes}
        start_nodes = [node for node in graph.nodes if node.node_type == WorkflowNodeType.start]
        end_nodes = [node for node in graph.nodes if node.node_type == WorkflowNodeType.end]
        if len(start_nodes) != 1:
            issues.append(
                _issue(
                    "invalid_start_count",
                    f"Workflow requires exactly one start node; found {len(start_nodes)}.",
                )
            )
        if len(end_nodes) != 1:
            issues.append(
                _issue(
                    "invalid_end_count",
                    f"Workflow requires exactly one end node; found {len(end_nodes)}.",
                )
            )

        outgoing: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, list[str]] = defaultdict(list)
        seen_pairs: set[tuple[str, str]] = set()
        valid_edges = []
        for edge in graph.edges:
            if edge.source_node_id not in unique_nodes:
                issues.append(
                    _edge_issue(
                        "unknown_edge_source",
                        f"Edge source '{edge.source_node_id}' does not exist.",
                        edge.edge_id,
                    )
                )
                continue
            if edge.target_node_id not in unique_nodes:
                issues.append(
                    _edge_issue(
                        "unknown_edge_target",
                        f"Edge target '{edge.target_node_id}' does not exist.",
                        edge.edge_id,
                    )
                )
                continue
            if edge.source_node_id == edge.target_node_id:
                issues.append(_edge_issue("self_loop", "Self-loop edges are not allowed.", edge.edge_id))
                continue
            pair = (edge.source_node_id, edge.target_node_id)
            if pair in seen_pairs:
                issues.append(
                    _edge_issue(
                        "duplicate_edge_path",
                        f"Duplicate edge path '{pair[0]}' -> '{pair[1]}'.",
                        edge.edge_id,
                    )
                )
                continue
            seen_pairs.add(pair)
            if edge.condition:
                issues.append(
                    _edge_issue(
                        "unsupported_edge_condition",
                        "Conditional edges require the future branch-node DSL; free-form conditions are not executed.",
                        edge.edge_id,
                    )
                )
            valid_edges.append(edge)
            outgoing[edge.source_node_id].append(edge.target_node_id)
            incoming[edge.target_node_id].append(edge.source_node_id)

        for node in start_nodes:
            if incoming[node.node_id]:
                issues.append(_node_issue("start_has_incoming_edge", "Start node cannot have incoming edges.", node.node_id))
        for node in end_nodes:
            if outgoing[node.node_id]:
                issues.append(_node_issue("end_has_outgoing_edge", "End node cannot have outgoing edges.", node.node_id))

        for node in graph.nodes:
            issues.extend(self._validate_node_config(node))
        issues.extend(self._validate_variables(graph.variables))

        if len(start_nodes) == 1:
            reachable = _reachable(start_nodes[0].node_id, outgoing)
            for node_id in unique_nodes:
                if node_id not in reachable:
                    issues.append(_node_issue("unreachable_node", "Node is not reachable from start.", node_id))

        if len(end_nodes) == 1:
            reaches_end = _reachable(end_nodes[0].node_id, incoming)
            for node_id in unique_nodes:
                if node_id not in reaches_end:
                    issues.append(_node_issue("no_path_to_end", "Node has no path to the end node.", node_id))

        if _has_cycle(unique_nodes, valid_edges):
            issues.append(_issue("cycle_detected", "Workflow cycles are not supported by this DAG compiler."))

        return WorkflowValidationReport(
            valid=not any(issue.severity.value == "error" for issue in issues),
            issues=_deduplicate_issues(issues),
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            start_node_id=start_nodes[0].node_id if len(start_nodes) == 1 else None,
            end_node_id=end_nodes[0].node_id if len(end_nodes) == 1 else None,
        )

    def _validate_node_config(self, node: WorkflowNodeDefinition) -> list[WorkflowValidationIssue]:
        config = node.config
        if node.node_type == WorkflowNodeType.tool:
            if not _non_empty_string(config.get("tool_name")):
                return [_node_issue("tool_name_required", "Tool node requires config.tool_name.", node.node_id)]
            arguments = config.get("arguments", {})
            if not isinstance(arguments, dict):
                return [_node_issue("tool_arguments_invalid", "Tool config.arguments must be an object.", node.node_id)]

        if node.node_type == WorkflowNodeType.agent and not _non_empty_string(config.get("prompt")):
            return [_node_issue("agent_prompt_required", "Agent node requires config.prompt.", node.node_id)]

        if node.node_type == WorkflowNodeType.knowledge_retrieval:
            top_k = config.get("top_k", 3)
            if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
                return [_node_issue("knowledge_top_k_invalid", "Knowledge config.top_k must be an integer from 1 to 20.", node.node_id)]

        return []

    def _validate_variables(
        self,
        variables: dict[str, Any],
    ) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        supported_types = {"string", "integer", "number", "boolean", "object", "array"}
        for name, definition in variables.items():
            if not isinstance(definition, dict):
                issues.append(
                    _issue(
                        "variable_definition_invalid",
                        f"Variable '{name}' definition must be an object.",
                    )
                )
                continue
            variable_type = definition.get("type")
            if variable_type not in supported_types:
                issues.append(
                    _issue(
                        "variable_type_unsupported",
                        f"Variable '{name}' has unsupported type '{variable_type}'.",
                    )
                )
            if "required" in definition and not isinstance(definition["required"], bool):
                issues.append(
                    _issue(
                        "variable_required_invalid",
                        f"Variable '{name}' required flag must be boolean.",
                    )
                )
        return issues


def _reachable(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        queue.extend(adjacency[node_id])
    return visited


def _has_cycle(nodes: dict[str, Any], edges: list[Any]) -> bool:
    indegree = {node_id: 0 for node_id in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.source_node_id].append(edge.target_node_id)
        indegree[edge.target_node_id] += 1
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited != len(nodes)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _issue(code: str, message: str) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message)


def _node_issue(code: str, message: str, node_id: str) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_id=node_id)


def _edge_issue(code: str, message: str, edge_id: str) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, edge_id=edge_id)


def _deduplicate_issues(issues: list[WorkflowValidationIssue]) -> list[WorkflowValidationIssue]:
    unique: dict[tuple, WorkflowValidationIssue] = {}
    for issue in issues:
        key = (issue.code, issue.node_id, issue.edge_id, issue.message)
        unique.setdefault(key, issue)
    return list(unique.values())

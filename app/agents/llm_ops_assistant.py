import json
from typing import Any

from pydantic import BaseModel, Field

from app.llm import LLMClient
from app.schemas import ChatMessage, DiagnosisReport, MessageRole, SourceDocument, ToolCall, ToolResult
from app.tools import ToolRegistry


class OpsToolSelection(BaseModel):
    reasoning: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class OpsDiagnosisDraft(BaseModel):
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class LLMOpsAssistant:
    """LLM helper for ops tool selection and diagnosis summarization."""

    def __init__(self, llm: LLMClient, tool_registry: ToolRegistry) -> None:
        self.llm = llm
        self.tool_registry = tool_registry

    async def select_tool_calls(
        self,
        question: str,
        service: str,
        fallback_tool_calls: list[ToolCall],
    ) -> tuple[list[ToolCall], dict[str, Any]]:
        messages = [
            ChatMessage(
                role=MessageRole.system,
                content=(
                    "You are an OnCall ops planner. Choose the smallest useful set of tools. "
                    "Return JSON only with fields: reasoning, tool_calls. "
                    "Each tool call must have name and arguments."
                ),
            ),
            ChatMessage(
                role=MessageRole.user,
                content=json.dumps(
                    {
                        "question": question,
                        "service": service,
                        "available_tools": self.tool_registry.tool_schemas(),
                    },
                    ensure_ascii=False,
                ),
            ),
        ]

        try:
            selection = await self.llm.generate_json(messages=messages, schema=OpsToolSelection)
        except Exception as exc:
            return fallback_tool_calls, {
                "source": "fallback",
                "reason": "llm_error",
                "error": str(exc),
            }

        tool_calls = self._valid_tool_calls(selection.tool_calls, service=service)
        if not tool_calls:
            return fallback_tool_calls, {
                "source": "fallback",
                "reason": "empty_or_invalid_llm_tool_selection",
                "llm_reasoning": selection.reasoning,
            }

        return tool_calls, {
            "source": "llm",
            "reasoning": selection.reasoning,
            "selected_tools": [tool_call.model_dump() for tool_call in tool_calls],
        }

    async def summarize(
        self,
        question: str,
        service: str,
        tool_results: list[ToolResult],
        sources: list[SourceDocument],
        fallback_report: DiagnosisReport,
    ) -> tuple[DiagnosisReport, dict[str, Any]]:
        messages = [
            ChatMessage(
                role=MessageRole.system,
                content=(
                    "You are an OnCall diagnosis assistant. Summarize the incident using only "
                    "the provided tool results and runbook sources. Return JSON only with fields: "
                    "summary, evidence, recommendations, risks, confidence."
                ),
            ),
            ChatMessage(
                role=MessageRole.user,
                content=json.dumps(
                    {
                        "question": question,
                        "service": service,
                        "tool_results": [result.model_dump(mode="json") for result in tool_results],
                        "runbook_sources": [
                            source.model_dump(mode="json", exclude={"content"})
                            for source in sources
                        ],
                        "fallback_report": fallback_report.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            ),
        ]

        try:
            draft = await self.llm.generate_json(messages=messages, schema=OpsDiagnosisDraft)
        except Exception as exc:
            return fallback_report, {
                "source": "fallback",
                "reason": "llm_error",
                "error": str(exc),
            }

        if not draft.summary.strip():
            return fallback_report, {
                "source": "fallback",
                "reason": "empty_llm_summary",
            }

        report = DiagnosisReport(
            summary=draft.summary,
            evidence=draft.evidence or fallback_report.evidence,
            recommendations=draft.recommendations or fallback_report.recommendations,
            risks=draft.risks or fallback_report.risks,
            confidence=draft.confidence if draft.confidence is not None else fallback_report.confidence,
        )
        return report, {"source": "llm"}

    def _valid_tool_calls(self, tool_calls: list[ToolCall], service: str) -> list[ToolCall]:
        valid_calls: list[ToolCall] = []
        for tool_call in tool_calls:
            if self.tool_registry.get(tool_call.name) is None:
                continue

            arguments = dict(tool_call.arguments)
            arguments.setdefault("service", service)
            valid_calls.append(
                ToolCall(
                    name=tool_call.name,
                    arguments=arguments,
                    trace_id=tool_call.trace_id,
                )
            )

        return valid_calls

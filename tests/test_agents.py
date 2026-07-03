import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic import BaseModel

from app.agents import ConversationAgent, KnowledgeAgent, OpsAgent
from app.schemas import ChatMessage, ChatMode, ChatRequest, ToolCall
from app.storage import SQLiteIncidentStore
from app.tools import create_ops_tool_registry


class FakeOpsLLM:
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        return "fake"

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        yield "fake"

    async def generate_json(
        self,
        messages: Sequence[ChatMessage],
        schema: type[BaseModel],
        tools: list[dict[str, Any]] | None = None,
    ) -> BaseModel:
        if schema.__name__ == "OpsToolSelection":
            return schema(
                reasoning="Metrics and logs are enough for the first pass.",
                tool_calls=[
                    ToolCall(name="query_metrics", arguments={"window": "30m"}),
                    ToolCall(name="query_logs", arguments={"window": "30m"}),
                ],
            )

        if schema.__name__ == "OpsDiagnosisDraft":
            return schema(
                summary="LLM summary: payment-api 5xx is likely related to database connection pool exhaustion.",
                evidence=["LLM evidence: metrics and logs both show a related failure pattern."],
                recommendations=["LLM recommendation: check connection pool configuration first."],
                risks=["LLM risk: confirm before rollback or restart."],
                confidence=0.66,
            )

        return schema()


def test_knowledge_agent_answers_with_sources() -> None:
    agent = KnowledgeAgent.from_runbook_directory()

    response = asyncio.run(agent.answer("支付故障处理手册有哪些步骤", session_id="test"))

    assert response.mode == ChatMode.knowledge
    assert response.sources
    assert response.metadata["retrieved_count"] > 0


def test_ops_agent_returns_diagnosis_with_traces() -> None:
    agent = OpsAgent.create_default()

    response = asyncio.run(agent.analyze("payment 服务 5xx 升高怎么办", session_id="test"))

    assert response.mode == ChatMode.ops
    assert response.metadata["service"] == "payment-api"
    assert response.metadata["graph_trace"] == [
        "infer_service",
        "plan",
        "select_tools",
        "execute_tools",
        "retrieve_runbook",
        "build_fallback_report",
        "summarize_report",
        "build_response",
        "persist_incident",
    ]
    assert response.metadata["graph_runtime"]["requested"] == "local"
    assert response.metadata["graph_runtime"]["used"] == "local"
    assert len(response.metadata["react_steps"]) == 5
    assert len(response.metadata["plan_trace"]["plan"]) == 4
    assert "诊断结论" in response.answer


def test_ops_agent_persists_incident_history(tmp_path) -> None:
    store = SQLiteIncidentStore(tmp_path / "agent-incidents.db")
    agent = OpsAgent.create_default(incident_store=store)

    response = asyncio.run(agent.analyze("payment 5xx error rate is high", session_id="persist-test"))

    incident_id = response.metadata["incident_id"]
    diagnosis_id = response.metadata["diagnosis_id"]
    latest_diagnosis = store.get_latest_diagnosis(incident_id)

    assert store.get_incident(incident_id) is not None
    assert latest_diagnosis is not None
    assert latest_diagnosis.diagnosis_id == diagnosis_id


def test_ops_agent_uses_llm_for_tool_selection_and_summary(tmp_path) -> None:
    store = SQLiteIncidentStore(tmp_path / "llm-agent-incidents.db")
    agent = OpsAgent(
        tool_registry=create_ops_tool_registry(mode="mock"),
        knowledge_agent=KnowledgeAgent.from_runbook_directory(),
        incident_store=store,
        llm=FakeOpsLLM(),
    )

    response = asyncio.run(agent.analyze("payment 5xx error rate is high", session_id="llm-test"))

    assert response.metadata["llm_tool_selection"]["source"] == "llm"
    assert response.metadata["llm_summary"]["source"] == "llm"
    assert len(response.metadata["react_steps"]) == 3
    assert "LLM summary" in response.answer


def test_conversation_agent_routes_knowledge_and_ops() -> None:
    agent = ConversationAgent.create_default()

    ops_response = asyncio.run(
        agent.chat(ChatRequest(message="payment 服务 5xx 升高怎么办", session_id="route-test"))
    )
    knowledge_response = asyncio.run(
        agent.chat(ChatRequest(message="支付故障处理手册有哪些步骤", session_id="route-test"))
    )

    assert ops_response.mode == ChatMode.ops
    assert knowledge_response.mode == ChatMode.knowledge

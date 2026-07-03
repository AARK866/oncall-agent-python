import asyncio

from app.agents import ConversationAgent, KnowledgeAgent, OpsAgent
from app.schemas import ChatMode, ChatRequest


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
    assert len(response.metadata["react_steps"]) == 5
    assert len(response.metadata["plan_trace"]["plan"]) == 4
    assert "诊断结论" in response.answer


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

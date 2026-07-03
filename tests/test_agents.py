import asyncio

from app.agents import ConversationAgent, KnowledgeAgent, OpsAgent
from app.schemas import ChatMode, ChatRequest
from app.storage import SQLiteIncidentStore


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

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic import BaseModel

from app.agents import KnowledgeAgent
from app.rag import KnowledgeBase
from app.schemas import ChatMessage, ChatMode


class FailingLLM:
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        raise RuntimeError("llm unavailable")

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        raise RuntimeError("llm unavailable")
        yield ""

    async def generate_json(
        self,
        messages: Sequence[ChatMessage],
        schema: type[BaseModel],
        tools: list[dict[str, Any]] | None = None,
    ) -> BaseModel:
        raise RuntimeError("llm unavailable")


def test_vector_retrieval_failure_falls_back_to_keyword(monkeypatch) -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks", retriever_mode="vector")

    def fail_vector_search(*args, **kwargs):
        raise RuntimeError("vector store unavailable")

    monkeypatch.setattr(kb, "_vector_search", fail_vector_search)

    results = kb.search("payment 5xx database", service="payment-api", top_k=1)

    assert results
    assert results[0].metadata["retriever"] == "keyword"
    assert results[0].metadata["recovery"]["used"] is True
    assert results[0].metadata["recovery"]["fallback_from"] == "vector"
    assert results[0].metadata["recovery"]["fallback_to"] == "keyword"


def test_hybrid_retrieval_failure_falls_back_to_keyword(monkeypatch) -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks", retriever_mode="hybrid")

    def fail_hybrid_search(*args, **kwargs):
        raise RuntimeError("embedding service unavailable")

    monkeypatch.setattr(kb, "_hybrid_search", fail_hybrid_search)

    results = kb.search("payment 5xx database", service="payment-api", top_k=1)

    assert results
    assert results[0].metadata["recovery"]["fallback_from"] == "hybrid"


def test_knowledge_agent_uses_fallback_answer_when_llm_fails() -> None:
    agent = KnowledgeAgent(
        knowledge_base=KnowledgeBase.from_directory("app/data/runbooks"),
        llm=FailingLLM(),
    )

    response = asyncio.run(
        agent.answer(
            "payment-api 5xx is high",
            session_id="llm-fallback-test",
            service="payment-api",
            incident_type="5xx",
        )
    )

    assert response.mode == ChatMode.knowledge
    assert response.sources
    assert response.metadata["llm_fallback"]["used"] is True
    assert "fallback answer" in response.answer

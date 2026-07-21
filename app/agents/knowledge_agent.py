from pathlib import Path
from typing import Sequence

from app.llm import LLMClient, create_llm_client
from app.rag import KnowledgeBase
from app.schemas import ChatMessage, ChatMode, ChatResponse, MessageRole, SourceDocument


class KnowledgeAgent:
    """Agent that answers questions with knowledge-base context."""

    def __init__(self, knowledge_base: KnowledgeBase, llm: LLMClient | None = None) -> None:
        self.knowledge_base = knowledge_base
        self.llm = llm or create_llm_client()

    @classmethod
    def from_runbook_directory(
        cls,
        directory: str | Path = "app/data/runbooks",
        llm: LLMClient | None = None,
    ) -> "KnowledgeAgent":
        return cls(
            knowledge_base=KnowledgeBase.from_directory(directory),
            llm=llm,
        )

    async def answer(
        self,
        question: str,
        session_id: str = "default",
        top_k: int = 3,
        service: str | None = None,
        incident_type: str | None = None,
        keywords: list[str] | None = None,
    ) -> ChatResponse:
        sources = self.search(
            question=question,
            top_k=top_k,
            service=service,
            incident_type=incident_type,
            keywords=keywords,
        )
        if not sources:
            return ChatResponse(
                session_id=session_id,
                answer=(
                    "No relevant runbook content was found. Please add a service name, "
                    "alert symptom, or time window and try again."
                ),
                mode=ChatMode.knowledge,
                sources=[],
                metadata={"retrieved_count": 0, "knowledge_base": self.knowledge_base.stats()},
            )

        metadata = {
            "retrieved_count": len(sources),
            "knowledge_base": self.knowledge_base.stats(),
            **self._recovery_metadata(sources),
        }
        messages = self._build_messages(question=question, sources=sources)
        try:
            draft_answer = await self.llm.generate(messages)
        except Exception as exc:
            draft_answer = self._fallback_answer(question=question, sources=sources, error=exc)
            metadata["llm_fallback"] = {
                "used": True,
                "reason": "llm_error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }

        return ChatResponse(
            session_id=session_id,
            answer=self._format_answer(draft_answer=draft_answer, sources=sources),
            mode=ChatMode.knowledge,
            sources=sources,
            metadata=metadata,
        )

    def search(
        self,
        question: str,
        top_k: int = 3,
        service: str | None = None,
        incident_type: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[SourceDocument]:
        return self.knowledge_base.search(
            query=question,
            top_k=top_k,
            service=service,
            incident_type=incident_type,
            keywords=keywords,
        )

    def _build_messages(self, question: str, sources: Sequence[SourceDocument]) -> list[ChatMessage]:
        context = "\n\n".join(
            f"Source: {source.title}\nContent: {source.content}" for source in sources
        )
        return [
            ChatMessage(
                role=MessageRole.system,
                content=(
                    "You are an enterprise OnCall knowledge-base assistant. "
                    "Answer only from the provided runbook context. "
                    "If the context is insufficient, say what is missing."
                ),
            ),
            ChatMessage(
                role=MessageRole.user,
                content=f"Question: {question}\n\nRunbook context:\n{context}",
            ),
        ]

    def _format_answer(self, draft_answer: str, sources: Sequence[SourceDocument]) -> str:
        source_lines = [
            f"- {source.title} (score={source.score})" for source in sources
        ]
        return (
            f"{draft_answer}\n\n"
            "References:\n"
            f"{chr(10).join(source_lines)}"
        )

    def _fallback_answer(
        self,
        question: str,
        sources: Sequence[SourceDocument],
        error: Exception,
    ) -> str:
        snippets = []
        for source in sources[:3]:
            content = " ".join(source.content.split())
            snippets.append(f"- {source.title}: {content[:240]}")

        return (
            "The LLM call failed, so this fallback answer is built from retrieved runbook snippets.\n\n"
            f"Question: {question}\n\n"
            "Suggested next steps:\n"
            f"{chr(10).join(snippets)}\n\n"
            f"Fallback reason: {type(error).__name__}"
        )

    def _recovery_metadata(self, sources: Sequence[SourceDocument]) -> dict:
        recoveries = [
            source.metadata.get("recovery")
            for source in sources
            if isinstance(source.metadata.get("recovery"), dict)
        ]
        if not recoveries:
            return {}
        return {
            "retrieval_fallback": {
                "used": True,
                "recoveries": recoveries,
            }
        }

from pathlib import Path
from typing import Sequence

from app.llm import LLMClient, create_llm_client
from app.rag import LocalKnowledgeBase
from app.schemas import ChatMessage, ChatMode, ChatResponse, MessageRole, SourceDocument


class KnowledgeAgent:
    """Agent that answers questions with local knowledge-base context."""

    def __init__(self, knowledge_base: LocalKnowledgeBase, llm: LLMClient | None = None) -> None:
        self.knowledge_base = knowledge_base
        self.llm = llm or create_llm_client()

    @classmethod
    def from_runbook_directory(
        cls,
        directory: str | Path = "app/data/runbooks",
        llm: LLMClient | None = None,
    ) -> "KnowledgeAgent":
        return cls(
            knowledge_base=LocalKnowledgeBase.from_directory(directory),
            llm=llm,
        )

    async def answer(
        self,
        question: str,
        session_id: str = "default",
        top_k: int = 3,
    ) -> ChatResponse:
        sources = self.search(question=question, top_k=top_k)
        if not sources:
            return ChatResponse(
                session_id=session_id,
                answer="知识库中暂未找到相关内容。请补充服务名、告警现象或时间范围后再试。",
                mode=ChatMode.knowledge,
                sources=[],
                metadata={"retrieved_count": 0},
            )

        messages = self._build_messages(question=question, sources=sources)
        draft_answer = await self.llm.generate(messages)
        answer = self._format_answer(draft_answer=draft_answer, sources=sources)

        return ChatResponse(
            session_id=session_id,
            answer=answer,
            mode=ChatMode.knowledge,
            sources=sources,
            metadata={"retrieved_count": len(sources)},
        )

    def search(self, question: str, top_k: int = 3) -> list[SourceDocument]:
        return self.knowledge_base.search(query=question, top_k=top_k)

    def _build_messages(self, question: str, sources: Sequence[SourceDocument]) -> list[ChatMessage]:
        context = "\n\n".join(
            f"来源：{source.title}\n内容：{source.content}" for source in sources
        )
        return [
            ChatMessage(
                role=MessageRole.system,
                content=(
                    "你是企业 OnCall 知识库助手。请只基于给定知识库内容回答，"
                    "无法确认的信息要明确说明。"
                ),
            ),
            ChatMessage(
                role=MessageRole.user,
                content=f"问题：{question}\n\n知识库内容：\n{context}",
            ),
        ]

    def _format_answer(self, draft_answer: str, sources: Sequence[SourceDocument]) -> str:
        source_lines = [
            f"- {source.title}（score={source.score}）" for source in sources
        ]
        return (
            f"{draft_answer}\n\n"
            "参考来源：\n"
            f"{chr(10).join(source_lines)}"
        )

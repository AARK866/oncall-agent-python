from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from pydantic import BaseModel

from app.schemas import ChatMessage, MessageRole


class LLMClient(Protocol):
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a full assistant answer."""

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Generate assistant answer chunks."""

    async def generate_json(
        self,
        messages: Sequence[ChatMessage],
        schema: type[BaseModel],
        tools: list[dict[str, Any]] | None = None,
    ) -> BaseModel:
        """Generate a structured response matching a Pydantic schema."""


class MockLLMClient:
    """Deterministic local LLM replacement for development and tests."""

    def __init__(self, default_answer: str | None = None) -> None:
        self.default_answer = default_answer or (
            "这是 MockLLM 的本地回答。真实模型接入前，我会根据输入返回稳定的模拟结果。"
        )

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        last_user_message = self._last_user_message(messages)
        if not last_user_message:
            return self.default_answer

        return f"{self.default_answer}\n\n用户问题：{last_user_message}"

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        answer = await self.generate(messages=messages, tools=tools)
        for chunk in self._split_answer(answer):
            yield chunk

    async def generate_json(
        self,
        messages: Sequence[ChatMessage],
        schema: type[BaseModel],
        tools: list[dict[str, Any]] | None = None,
    ) -> BaseModel:
        return schema.model_construct()

    def _last_user_message(self, messages: Sequence[ChatMessage]) -> str | None:
        for message in reversed(messages):
            if message.role == MessageRole.user:
                return message.content
        return None

    def _split_answer(self, answer: str, chunk_size: int = 16) -> list[str]:
        return [answer[index : index + chunk_size] for index in range(0, len(answer), chunk_size)]

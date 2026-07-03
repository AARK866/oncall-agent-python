import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.config import settings
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
        try:
            return schema()
        except Exception:
            return schema.model_construct()

    def _last_user_message(self, messages: Sequence[ChatMessage]) -> str | None:
        for message in reversed(messages):
            if message.role == MessageRole.user:
                return message.content
        return None

    def _split_answer(self, answer: str, chunk_size: int = 16) -> list[str]:
        return [answer[index : index + chunk_size] for index in range(0, len(answer), chunk_size)]


class OpenAICompatibleLLMClient:
    """LLM client for OpenAI-compatible chat completions APIs."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_to_dict(message) for message in messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return data["choices"][0]["message"].get("content") or ""

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_to_dict(message) for message in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    chunk = self._parse_stream_line(line)
                    if chunk:
                        yield chunk

    async def generate_json(
        self,
        messages: Sequence[ChatMessage],
        schema: type[BaseModel],
        tools: list[dict[str, Any]] | None = None,
    ) -> BaseModel:
        answer = await self.generate(messages=messages, tools=tools)
        return schema.model_validate_json(self._extract_json(answer))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _message_to_dict(self, message: ChatMessage) -> dict[str, str]:
        return {
            "role": message.role.value,
            "content": message.content,
        }

    def _parse_stream_line(self, line: str) -> str | None:
        if not line.startswith("data: "):
            return None

        raw_data = line.removeprefix("data: ").strip()
        if raw_data == "[DONE]":
            return None

        data = json.loads(raw_data)
        return data["choices"][0].get("delta", {}).get("content")

    def _extract_json(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end >= start:
            return stripped[start : end + 1]

        return stripped


def create_llm_client() -> LLMClient:
    provider = settings.llm_provider.lower().strip()
    if provider in {"mock", "local"}:
        return MockLLMClient()

    if provider in {"openai", "openai-compatible", "deepseek", "qwen"}:
        if not settings.llm_api_key:
            raise ValueError("LLM_API_KEY is required when LLM_PROVIDER is not mock.")
        return OpenAICompatibleLLMClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")

import asyncio

import pytest
from pydantic import BaseModel

from app.llm import MockLLMClient, OpenAICompatibleLLMClient, create_llm_client
from app.schemas import ChatMessage, MessageRole


class ExampleStructuredOutput(BaseModel):
    answer: str = "default"


def test_create_llm_client_defaults_to_mock() -> None:
    client = create_llm_client()

    assert isinstance(client, MockLLMClient)


def test_openai_compatible_message_conversion() -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.com/v1/",
    )

    message = ChatMessage(role=MessageRole.user, content="hello")

    assert client._message_to_dict(message) == {
        "role": "user",
        "content": "hello",
    }
    assert client.base_url == "https://example.com/v1"


def test_openai_compatible_stream_parser() -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.com/v1",
    )
    line = 'data: {"choices":[{"delta":{"content":"hello"}}]}'

    assert client._parse_stream_line(line) == "hello"
    assert client._parse_stream_line("data: [DONE]") is None
    assert client._parse_stream_line("") is None


def test_mock_llm_generate_json_returns_schema_defaults() -> None:
    client = MockLLMClient()

    result = asyncio.run(client.generate_json(messages=[], schema=ExampleStructuredOutput))

    assert isinstance(result, ExampleStructuredOutput)
    assert result.answer == "default"


def test_openai_compatible_extracts_json_from_markdown_fence() -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.com/v1",
    )

    assert client._extract_json('```json\n{"answer":"ok"}\n```') == '{"answer":"ok"}'

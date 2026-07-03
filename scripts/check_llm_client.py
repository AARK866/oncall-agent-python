import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.llm import create_llm_client
from app.schemas import ChatMessage, MessageRole


async def main() -> int:
    print("LLM configuration")
    print(f"- provider: {settings.llm_provider}")
    print(f"- model: {settings.llm_model}")
    print(f"- base_url: {settings.llm_base_url}")
    print(f"- timeout_seconds: {settings.llm_timeout_seconds}")
    print(f"- api_key_set: {bool(settings.llm_api_key)}")

    try:
        client = create_llm_client()
    except Exception as exc:
        print("")
        print("Failed to create LLM client.")
        print(f"Reason: {exc}")
        return 1

    messages = [
        ChatMessage(
            role=MessageRole.system,
            content="You are a concise OnCall Agent configuration checker.",
        ),
        ChatMessage(
            role=MessageRole.user,
            content="Reply with one short sentence confirming the LLM client works.",
        ),
    ]

    try:
        answer = await client.generate(messages)
    except Exception as exc:
        print("")
        print("Failed to call LLM provider.")
        print(f"Reason: {exc}")
        return 1

    print("")
    print("LLM response")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

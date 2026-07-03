import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agents import ConversationAgent
from app.schemas import AgentEvent, AgentEventType, ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])
conversation_agent = ConversationAgent.create_default()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await conversation_agent.chat(request)


@router.post("/chat/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_chat_events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _stream_chat_events(request: ChatRequest) -> AsyncIterator[str]:
    yield _format_sse(
        AgentEvent(
            event=AgentEventType.thinking,
            session_id=request.session_id,
            data={"text": "正在分析问题类型并准备执行。"},
        )
    )

    response = await conversation_agent.chat(request)

    for step in response.metadata.get("react_steps", []):
        yield _format_sse(
            AgentEvent(
                event=AgentEventType.thinking,
                session_id=request.session_id,
                data={"text": step.get("thought", "")},
            )
        )

        action = step.get("action")
        if action:
            yield _format_sse(
                AgentEvent(
                    event=AgentEventType.tool_call,
                    session_id=request.session_id,
                    data=action,
                )
            )

        observation = step.get("observation")
        if observation:
            yield _format_sse(
                AgentEvent(
                    event=AgentEventType.tool_result,
                    session_id=request.session_id,
                    data=observation,
                )
            )

    if response.sources:
        yield _format_sse(
            AgentEvent(
                event=AgentEventType.retrieved_docs,
                session_id=request.session_id,
                data={"sources": [source.model_dump(mode="json") for source in response.sources]},
            )
        )

    yield _format_sse(
        AgentEvent(
            event=AgentEventType.final,
            session_id=request.session_id,
            data=response.model_dump(mode="json"),
        )
    )


def _format_sse(event: AgentEvent) -> str:
    data = json.dumps(event.data, ensure_ascii=False)
    return f"event: {event.event.value}\ndata: {data}\n\n"

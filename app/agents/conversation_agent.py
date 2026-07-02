from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.ops_agent import OpsAgent
from app.llm import LLMClient, MockLLMClient
from app.memory import InMemoryConversationMemory
from app.schemas import ChatMode, ChatRequest, ChatResponse


class ConversationAgent:
    """Entry agent that routes user messages to the right specialist."""

    def __init__(
        self,
        knowledge_agent: KnowledgeAgent,
        ops_agent: OpsAgent | None = None,
        memory: InMemoryConversationMemory | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.knowledge_agent = knowledge_agent
        self.ops_agent = ops_agent or OpsAgent.create_default()
        self.memory = memory or InMemoryConversationMemory()
        self.llm = llm or MockLLMClient()

    @classmethod
    def create_default(cls) -> "ConversationAgent":
        return cls(knowledge_agent=KnowledgeAgent.from_runbook_directory())

    async def chat(self, request: ChatRequest) -> ChatResponse:
        mode = self._resolve_mode(request)
        self.memory.add_user_message(session_id=request.session_id, content=request.message)

        if mode == ChatMode.knowledge:
            response = await self.knowledge_agent.answer(
                question=request.message,
                session_id=request.session_id,
            )
        elif mode == ChatMode.ops:
            response = await self.ops_agent.analyze(
                question=request.message,
                session_id=request.session_id,
            )
        else:
            response = await self._general_response(request)

        self.memory.add_assistant_message(session_id=request.session_id, content=response.answer)
        return response

    def _resolve_mode(self, request: ChatRequest) -> ChatMode:
        if request.mode != ChatMode.auto:
            return request.mode

        message = request.message.lower()
        if self._contains_any(message, self._knowledge_keywords()):
            return ChatMode.knowledge
        if self._contains_any(message, self._ops_keywords()):
            return ChatMode.ops

        return ChatMode.auto

    async def _general_response(self, request: ChatRequest) -> ChatResponse:
        messages = self.memory.get_messages(request.session_id)
        answer = await self.llm.generate(messages)
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            mode=ChatMode.auto,
            metadata={"routed_to": "mock_llm"},
        )

    def _contains_any(self, text: str, keywords: set[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    def _ops_keywords(self) -> set[str]:
        return {
            "5xx",
            "error",
            "错误率",
            "告警",
            "报警",
            "异常",
            "故障",
            "排查",
            "日志",
            "发布",
            "延迟",
            "timeout",
            "cpu",
            "内存",
            "连接池",
        }

    def _knowledge_keywords(self) -> set[str]:
        return {
            "手册",
            "文档",
            "知识库",
            "sop",
            "faq",
            "runbook",
        }

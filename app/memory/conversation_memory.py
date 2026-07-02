from collections import defaultdict

from app.schemas import ChatMessage, MessageRole


class InMemoryConversationMemory:
    """Simple session-based conversation memory for local development."""

    def __init__(self, max_messages_per_session: int = 20) -> None:
        self.max_messages_per_session = max_messages_per_session
        self._messages: dict[str, list[ChatMessage]] = defaultdict(list)

    def add_message(self, session_id: str, message: ChatMessage) -> None:
        messages = self._messages[session_id]
        messages.append(message)
        self._trim(session_id)

    def add_user_message(self, session_id: str, content: str) -> ChatMessage:
        message = ChatMessage(role=MessageRole.user, content=content)
        self.add_message(session_id=session_id, message=message)
        return message

    def add_assistant_message(self, session_id: str, content: str) -> ChatMessage:
        message = ChatMessage(role=MessageRole.assistant, content=content)
        self.add_message(session_id=session_id, message=message)
        return message

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        return list(self._messages.get(session_id, []))

    def clear(self, session_id: str) -> None:
        self._messages.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        return list(self._messages.keys())

    def _trim(self, session_id: str) -> None:
        messages = self._messages[session_id]
        if len(messages) > self.max_messages_per_session:
            self._messages[session_id] = messages[-self.max_messages_per_session :]

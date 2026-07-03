from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChatMode(str, Enum):
    auto = "auto"
    knowledge = "knowledge"
    ops = "ops"


class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class AlertSeverity(str, Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class AgentEventType(str, Enum):
    thinking = "thinking"
    tool_call = "tool_call"
    tool_result = "tool_result"
    retrieved_docs = "retrieved_docs"
    answer_delta = "answer_delta"
    final = "final"
    error = "error"


class ChatMessage(BaseModel):
    role: MessageRole
    content: str
    name: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="default")
    mode: ChatMode = Field(default=ChatMode.auto)
    stream: bool = Field(default=False)


class SourceDocument(BaseModel):
    doc_id: str
    title: str
    content: str
    source: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    mode: ChatMode
    sources: list[SourceDocument] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertAnalyzeRequest(BaseModel):
    alert_id: str
    title: str
    service: str
    severity: AlertSeverity = Field(default=AlertSeverity.warning)
    start_time: datetime | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int | None = None


class ReactStep(BaseModel):
    thought: str
    action: ToolCall | None = None
    observation: ToolResult | None = None


class AgentEvent(BaseModel):
    event: AgentEventType
    data: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DiagnosisReport(BaseModel):
    summary: str
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

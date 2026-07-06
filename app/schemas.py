from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class IncidentStatus(str, Enum):
    open = "open"
    investigating = "investigating"
    resolved = "resolved"


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


class KnowledgeDocumentSummary(BaseModel):
    doc_id: str
    title: str
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentDetail(KnowledgeDocumentSummary):
    content: str


class KnowledgeStatsResponse(BaseModel):
    document_count: int
    chunk_count: int
    retriever_mode: str
    vector_store: str | None = None
    services: list[str] = Field(default_factory=list)
    incident_types: list[str] = Field(default_factory=list)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    service: str | None = None
    incident_type: str | None = None
    keywords: list[str] = Field(default_factory=list)


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[SourceDocument] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    mode: ChatMode
    sources: list[SourceDocument] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertTriggerResponse(BaseModel):
    received: int
    processed: int
    results: list[ChatResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertAnalyzeRequest(BaseModel):
    alert_id: str
    title: str
    service: str
    severity: AlertSeverity = Field(default=AlertSeverity.warning)
    start_time: datetime | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="firing")
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = Field(default=None, alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    fingerprint: str | None = None


class AlertmanagerWebhookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str | None = None
    group_key: str | None = Field(default=None, alias="groupKey")
    status: str | None = None
    receiver: str | None = None
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(default_factory=dict, alias="commonAnnotations")
    external_url: str | None = Field(default=None, alias="externalURL")
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


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


class ToolBackendStatus(BaseModel):
    name: str
    configured: bool
    required_settings: list[str] = Field(default_factory=list)
    optional_settings: list[str] = Field(default_factory=list)
    missing_settings: list[str] = Field(default_factory=list)
    notes: str | None = None


class OpsToolHealthResponse(BaseModel):
    mode: str
    connector_name: str
    ready: bool
    tools: list[str] = Field(default_factory=list)
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    backends: list[ToolBackendStatus] = Field(default_factory=list)
    message: str


class ReactStep(BaseModel):
    thought: str
    action: ToolCall | None = None
    observation: ToolResult | None = None


class PlanStep(BaseModel):
    step_id: str
    goal: str
    tool_call: ToolCall | None = None
    status: str = "pending"
    observation: ToolResult | None = None


class PlanTrace(BaseModel):
    plan: list[PlanStep] = Field(default_factory=list)
    replan_notes: list[str] = Field(default_factory=list)


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


class IncidentRecord(BaseModel):
    incident_id: str
    title: str
    service: str
    question: str
    session_id: str
    severity: AlertSeverity = AlertSeverity.warning
    status: IncidentStatus = IncidentStatus.investigating
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DiagnosisRecord(BaseModel):
    diagnosis_id: str
    incident_id: str
    answer: str
    mode: ChatMode
    sources: list[SourceDocument] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    react_steps: list[ReactStep] = Field(default_factory=list)
    plan_trace: PlanTrace | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IncidentDetailResponse(BaseModel):
    incident: IncidentRecord
    latest_diagnosis: DiagnosisRecord | None = None

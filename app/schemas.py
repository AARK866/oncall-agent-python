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


class DiagnosisTaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_review = "waiting_review"
    cancel_requested = "cancel_requested"
    canceled = "canceled"
    timed_out = "timed_out"
    succeeded = "succeeded"
    failed = "failed"


class AlertGroupStatus(str, Enum):
    active = "active"
    resolved = "resolved"


class DiagnosisTaskEventType(str, Enum):
    queued = "queued"
    running = "running"
    waiting_review = "waiting_review"
    rerun_requested = "rerun_requested"
    resume_requested = "resume_requested"
    cancel_requested = "cancel_requested"
    canceled = "canceled"
    timed_out = "timed_out"
    graph_node_started = "graph_node_started"
    graph_node_completed = "graph_node_completed"
    graph_node_paused = "graph_node_paused"
    graph_node_canceled = "graph_node_canceled"
    graph_node_failed = "graph_node_failed"
    human_review_requested = "human_review_requested"
    human_review_approved = "human_review_approved"
    human_review_rejected = "human_review_rejected"
    tool_result = "tool_result"
    retrieved_docs = "retrieved_docs"
    incident_persisted = "incident_persisted"
    succeeded = "succeeded"
    failed = "failed"


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
    knowledge_engine: str = "local"
    reranker: str = "none"
    rerank_candidate_multiplier: int = 1
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


class KnowledgeIngestSource(str, Enum):
    local = "local"
    github = "github"


class KnowledgeIngestRequest(BaseModel):
    source: KnowledgeIngestSource | None = None
    path: str | None = None
    chunk_size: int = Field(default=800, ge=100, le=8000)
    chunk_overlap: int = Field(default=120, ge=0, le=2000)
    full_rebuild: bool = False


class KnowledgeIngestResponse(BaseModel):
    status: str
    source: KnowledgeIngestSource
    path: str
    documents_loaded: int
    chunks_created: int
    vector_store: str
    collection_name: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeIngestionTaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class KnowledgeIngestionTaskRecord(BaseModel):
    task_id: str
    status: KnowledgeIngestionTaskStatus
    request: KnowledgeIngestRequest
    attempt: int = Field(default=0, ge=0)
    progress_stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    result: KnowledgeIngestResponse | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class KnowledgeIngestionAttemptRecord(BaseModel):
    task_id: str
    attempt: int = Field(ge=1)
    status: KnowledgeIngestionTaskStatus
    progress_stage: str
    result: KnowledgeIngestResponse | None = None
    error: str | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    started_at: datetime
    finished_at: datetime | None = None


class KnowledgeIngestionMetricsResponse(BaseModel):
    window_hours: int
    total_tasks: int
    by_status: dict[str, int] = Field(default_factory=dict)
    success_rate: float = Field(ge=0.0, le=1.0)
    average_duration_ms: float | None = None
    p95_duration_ms: int | None = None
    retried_tasks: int = 0
    total_attempts: int = 0
    documents_processed: int = 0
    chunks_created: int = 0
    vectors_upserted: int = 0
    stale_vectors_deleted: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeIngestionRetryRequest(BaseModel):
    requested_by: str = Field(default="manual", min_length=1, max_length=120)


class WorkflowApplicationStatus(str, Enum):
    active = "active"
    archived = "archived"


class WorkflowNodeType(str, Enum):
    start = "start"
    agent = "agent"
    knowledge_retrieval = "knowledge_retrieval"
    tool = "tool"
    human_review = "human_review"
    end = "end"


class WorkflowNodePosition(BaseModel):
    x: float = 0
    y: float = 0


class WorkflowNodeDefinition(BaseModel):
    node_id: str = Field(min_length=1, max_length=120)
    node_type: WorkflowNodeType
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    position: WorkflowNodePosition = Field(default_factory=WorkflowNodePosition)


class WorkflowEdgeDefinition(BaseModel):
    edge_id: str = Field(min_length=1, max_length=120)
    source_node_id: str = Field(min_length=1, max_length=120)
    target_node_id: str = Field(min_length=1, max_length=120)
    condition: str | None = Field(default=None, max_length=1000)
    priority: int = Field(default=0, ge=0)


class WorkflowGraphDefinition(BaseModel):
    schema_version: str = "1.0"
    nodes: list[WorkflowNodeDefinition] = Field(default_factory=list)
    edges: list[WorkflowEdgeDefinition] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class WorkflowApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class WorkflowApplicationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: WorkflowApplicationStatus | None = None


class WorkflowApplicationRecord(BaseModel):
    app_id: str
    name: str
    description: str = ""
    status: WorkflowApplicationStatus = WorkflowApplicationStatus.active
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowDraftUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    graph: WorkflowGraphDefinition


class WorkflowDraftRecord(BaseModel):
    draft_id: str
    app_id: str
    revision: int = Field(ge=1)
    graph: WorkflowGraphDefinition = Field(default_factory=WorkflowGraphDefinition)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowPublishRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    published_by: str = Field(default="manual", min_length=1, max_length=120)
    release_notes: str = Field(default="", max_length=4000)


class WorkflowVersionRecord(BaseModel):
    version_id: str
    app_id: str
    version_number: int = Field(ge=1)
    source_draft_revision: int = Field(ge=1)
    graph: WorkflowGraphDefinition
    graph_sha256: str = Field(min_length=64, max_length=64)
    release_notes: str = ""
    published_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowVersionRollbackRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    requested_by: str = Field(default="manual", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)


class WorkflowVersionRollbackResponse(BaseModel):
    version: WorkflowVersionRecord
    draft: WorkflowDraftRecord
    requested_by: str
    reason: str = ""


class WorkflowValidationSeverity(str, Enum):
    error = "error"
    warning = "warning"


class WorkflowValidationIssue(BaseModel):
    code: str
    message: str
    severity: WorkflowValidationSeverity = WorkflowValidationSeverity.error
    node_id: str | None = None
    edge_id: str | None = None


class WorkflowValidationReport(BaseModel):
    valid: bool
    issues: list[WorkflowValidationIssue] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    start_node_id: str | None = None
    end_node_id: str | None = None


class WorkflowDraftRunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = Field(default=None, max_length=200)


class WorkflowRunStatus(str, Enum):
    succeeded = "succeeded"
    waiting_review = "waiting_review"


class WorkflowExecutionSource(str, Enum):
    draft = "draft"
    published = "published"


class WorkflowDraftRunResponse(BaseModel):
    app_id: str
    draft_revision: int
    thread_id: str
    status: WorkflowRunStatus
    execution_source: WorkflowExecutionSource = WorkflowExecutionSource.draft
    version_number: int | None = Field(default=None, ge=1)
    output: Any = None
    node_outputs: dict[str, Any] = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)
    review_requests: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    mode: ChatMode
    sources: list[SourceDocument] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiagnosisTaskRecord(BaseModel):
    task_id: str
    alert_group_id: str | None = None
    rerun_of_task_id: str | None = None
    resume_of_task_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    source: str
    status: DiagnosisTaskStatus
    question: str
    session_id: str
    service: str | None = None
    severity: AlertSeverity = AlertSeverity.warning
    labels: dict[str, str] = Field(default_factory=dict)
    trigger_metadata: dict[str, Any] = Field(default_factory=dict)
    result: ChatResponse | None = None
    incident_id: str | None = None
    diagnosis_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AlertGroupRecord(BaseModel):
    group_id: str
    dedupe_key: str
    source: str
    title: str
    service: str | None = None
    severity: AlertSeverity = AlertSeverity.warning
    status: AlertGroupStatus = AlertGroupStatus.active
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    trigger_count: int = Field(default=1, ge=1)
    latest_task_id: str | None = None
    incident_id: str | None = None
    diagnosis_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)


class DiagnosisTaskEventRecord(BaseModel):
    event_id: str
    task_id: str
    event_type: DiagnosisTaskEventType
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OpsGraphCheckpointRecord(BaseModel):
    checkpoint_id: str
    task_id: str
    thread_id: str | None = None
    run_id: str | None = None
    node_name: str
    status: str
    state: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HumanReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class HumanReviewRequestRecord(BaseModel):
    review_id: str
    task_id: str
    service: str | None = None
    status: HumanReviewStatus = HumanReviewStatus.pending
    proposed_actions: list[str] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reviewer: str | None = None
    decision_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: datetime | None = None


class HumanReviewDecisionRequest(BaseModel):
    reviewer: str = Field(default="manual")
    reason: str | None = None


class DiagnosisTaskRerunRequest(BaseModel):
    requested_by: str = Field(default="manual", min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=1000)
    force: bool = False


class DiagnosisTaskResumeRequest(BaseModel):
    requested_by: str = Field(default="manual", min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=1000)
    force: bool = False


class DiagnosisTaskCancelRequest(BaseModel):
    requested_by: str = Field(default="manual", min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=1000)


class StaleTaskRecoveryRequest(BaseModel):
    requested_by: str = Field(default="system", min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=1000)
    max_age_seconds: int | None = Field(default=None, ge=1, le=86400)
    limit: int | None = Field(default=None, ge=1, le=500)


class StaleTaskRecoveryResponse(BaseModel):
    recovered: int
    tasks: list[DiagnosisTaskRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertTriggerResponse(BaseModel):
    received: int
    processed: int
    tasks: list[DiagnosisTaskRecord] = Field(default_factory=list)
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

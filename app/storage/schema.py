from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    desc,
    text,
)


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


incidents = Table(
    "incidents",
    metadata,
    Column("incident_id", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("service", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("session_id", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("labels_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index(
    "idx_incidents_service_status_created",
    incidents.c.service,
    incidents.c.status,
    desc(incidents.c.created_at),
)

diagnoses = Table(
    "diagnoses",
    metadata,
    Column("diagnosis_id", Text, primary_key=True),
    Column(
        "incident_id",
        Text,
        ForeignKey("incidents.incident_id"),
        nullable=False,
    ),
    Column("answer", Text, nullable=False),
    Column("mode", Text, nullable=False),
    Column("sources_json", Text, nullable=False),
    Column("tool_results_json", Text, nullable=False),
    Column("react_steps_json", Text, nullable=False),
    Column("plan_trace_json", Text),
    Column("metadata_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
Index(
    "idx_diagnoses_incident_created",
    diagnoses.c.incident_id,
    desc(diagnoses.c.created_at),
)

knowledge_index_manifest = Table(
    "knowledge_index_manifest",
    metadata,
    Column("namespace", Text, primary_key=True),
    Column("doc_id", Text, primary_key=True),
    Column("source_uri", Text, nullable=False),
    Column("source_version", Text, nullable=False),
    Column("document_signature", Text, nullable=False),
    Column("index_signature", Text, nullable=False),
    Column("chunk_ids_json", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("indexed_at", Text, nullable=False),
)
Index(
    "idx_knowledge_manifest_namespace",
    knowledge_index_manifest.c.namespace,
)

knowledge_ingestion_tasks = Table(
    "knowledge_ingestion_tasks",
    metadata,
    Column("task_id", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("request_json", Text, nullable=False),
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    Column("progress_stage", Text, nullable=False),
    Column("progress_percent", Integer, nullable=False, server_default=text("0")),
    Column("result_json", Text),
    Column("error", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("started_at", Text),
    Column("finished_at", Text),
)
Index(
    "idx_knowledge_ingestion_tasks_status_created",
    knowledge_ingestion_tasks.c.status,
    desc(knowledge_ingestion_tasks.c.created_at),
)

knowledge_ingestion_attempts = Table(
    "knowledge_ingestion_attempts",
    metadata,
    Column(
        "task_id",
        Text,
        ForeignKey("knowledge_ingestion_tasks.task_id"),
        primary_key=True,
    ),
    Column("attempt", Integer, primary_key=True),
    Column("status", Text, nullable=False),
    Column("progress_stage", Text, nullable=False),
    Column("result_json", Text),
    Column("error", Text),
    Column("elapsed_ms", Integer),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
)
Index(
    "idx_knowledge_ingestion_attempts_status",
    knowledge_ingestion_attempts.c.status,
    desc(knowledge_ingestion_attempts.c.started_at),
)

diagnosis_tasks = Table(
    "diagnosis_tasks",
    metadata,
    Column("task_id", Text, primary_key=True),
    Column("alert_group_id", Text),
    Column("rerun_of_task_id", Text),
    Column("resume_of_task_id", Text),
    Column("thread_id", Text),
    Column("run_id", Text),
    Column("source", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("session_id", Text, nullable=False),
    Column("service", Text),
    Column("severity", Text, nullable=False),
    Column("labels_json", Text, nullable=False),
    Column("trigger_metadata_json", Text, nullable=False),
    Column("result_json", Text),
    Column("incident_id", Text),
    Column("diagnosis_id", Text),
    Column("error", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("started_at", Text),
    Column("finished_at", Text),
)
Index(
    "idx_diagnosis_tasks_rerun_of",
    diagnosis_tasks.c.rerun_of_task_id,
    diagnosis_tasks.c.created_at,
)
Index(
    "idx_diagnosis_tasks_resume_of",
    diagnosis_tasks.c.resume_of_task_id,
    diagnosis_tasks.c.created_at,
)
Index(
    "idx_diagnosis_tasks_thread_created",
    diagnosis_tasks.c.thread_id,
    diagnosis_tasks.c.created_at,
)
Index(
    "idx_diagnosis_tasks_status_updated",
    diagnosis_tasks.c.status,
    diagnosis_tasks.c.updated_at,
)

alert_groups = Table(
    "alert_groups",
    metadata,
    Column("group_id", Text, primary_key=True),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("source", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("service", Text),
    Column("severity", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("labels_json", Text, nullable=False),
    Column("annotations_json", Text, nullable=False),
    Column("trigger_count", Integer, nullable=False),
    Column("latest_task_id", Text),
    Column("incident_id", Text),
    Column("diagnosis_id", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
)
Index(
    "idx_alert_groups_status_last_seen",
    alert_groups.c.status,
    desc(alert_groups.c.last_seen_at),
)

diagnosis_task_events = Table(
    "diagnosis_task_events",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column(
        "task_id",
        Text,
        ForeignKey("diagnosis_tasks.task_id"),
        nullable=False,
    ),
    Column("event_type", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("data_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
Index(
    "idx_diagnosis_task_events_task_created",
    diagnosis_task_events.c.task_id,
    diagnosis_task_events.c.created_at,
)

ops_graph_checkpoints = Table(
    "ops_graph_checkpoints",
    metadata,
    Column("checkpoint_id", Text, primary_key=True),
    Column(
        "task_id",
        Text,
        ForeignKey("diagnosis_tasks.task_id"),
        nullable=False,
    ),
    Column("thread_id", Text),
    Column("run_id", Text),
    Column("node_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("state_json", Text, nullable=False),
    Column("error", Text),
    Column("created_at", Text, nullable=False),
)
Index(
    "idx_ops_graph_checkpoints_task_created",
    ops_graph_checkpoints.c.task_id,
    ops_graph_checkpoints.c.created_at,
)
Index(
    "idx_ops_graph_checkpoints_thread_run",
    ops_graph_checkpoints.c.thread_id,
    ops_graph_checkpoints.c.run_id,
    ops_graph_checkpoints.c.created_at,
)

human_review_requests = Table(
    "human_review_requests",
    metadata,
    Column("review_id", Text, primary_key=True),
    Column(
        "task_id",
        Text,
        ForeignKey("diagnosis_tasks.task_id"),
        nullable=False,
    ),
    Column("service", Text),
    Column("status", Text, nullable=False),
    Column("proposed_actions_json", Text, nullable=False),
    Column("risk_reasons_json", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("reviewer", Text),
    Column("decision_reason", Text),
    Column("created_at", Text, nullable=False),
    Column("decided_at", Text),
)
Index(
    "idx_human_review_requests_status_created",
    human_review_requests.c.status,
    human_review_requests.c.created_at,
)
Index(
    "idx_human_review_requests_task_created",
    human_review_requests.c.task_id,
    human_review_requests.c.created_at,
)

workflow_applications = Table(
    "workflow_applications",
    metadata,
    Column("app_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=text("''")),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index(
    "idx_workflow_applications_status_updated",
    workflow_applications.c.status,
    desc(workflow_applications.c.updated_at),
)

workflow_drafts = Table(
    "workflow_drafts",
    metadata,
    Column("draft_id", Text, primary_key=True),
    Column(
        "app_id",
        Text,
        ForeignKey("workflow_applications.app_id"),
        nullable=False,
        unique=True,
    ),
    Column("revision", Integer, nullable=False),
    Column("graph_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

workflow_versions = Table(
    "workflow_versions",
    metadata,
    Column("version_id", Text, primary_key=True),
    Column(
        "app_id",
        Text,
        ForeignKey("workflow_applications.app_id"),
        nullable=False,
    ),
    Column("version_number", Integer, nullable=False),
    Column("source_draft_revision", Integer, nullable=False),
    Column("graph_json", Text, nullable=False),
    Column("graph_sha256", Text, nullable=False),
    Column("release_notes", Text, nullable=False, server_default=text("''")),
    Column("published_by", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    UniqueConstraint(
        "app_id",
        "version_number",
        name="uq_workflow_versions_app_version",
    ),
    UniqueConstraint(
        "app_id",
        "source_draft_revision",
        name="uq_workflow_versions_app_draft_revision",
    ),
)
Index(
    "idx_workflow_versions_app_version",
    workflow_versions.c.app_id,
    desc(workflow_versions.c.version_number),
)

workflow_runs = Table(
    "workflow_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column(
        "app_id",
        Text,
        ForeignKey("workflow_applications.app_id"),
        nullable=False,
    ),
    Column("execution_source", Text, nullable=False),
    Column("draft_revision", Integer, nullable=False),
    Column("version_number", Integer),
    Column("thread_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("inputs_json", Text, nullable=False),
    Column("output_json", Text),
    Column("error", Text),
    Column("started_by", Text, nullable=False),
    Column("graph_json", Text, nullable=False),
    Column("graph_sha256", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("finished_at", Text),
)
Index(
    "idx_workflow_runs_app_created",
    workflow_runs.c.app_id,
    desc(workflow_runs.c.created_at),
)
Index(
    "idx_workflow_runs_app_status",
    workflow_runs.c.app_id,
    workflow_runs.c.status,
    desc(workflow_runs.c.created_at),
)

workflow_run_events = Table(
    "workflow_run_events",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column(
        "run_id",
        Text,
        ForeignKey("workflow_runs.run_id"),
        nullable=False,
    ),
    Column("event_type", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("node_id", Text),
    Column("data_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
Index(
    "idx_workflow_run_events_run_created",
    workflow_run_events.c.run_id,
    workflow_run_events.c.created_at,
)

workflow_review_requests = Table(
    "workflow_review_requests",
    metadata,
    Column("review_id", Text, primary_key=True),
    Column(
        "run_id",
        Text,
        ForeignKey("workflow_runs.run_id"),
        nullable=False,
    ),
    Column("node_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("reviewer", Text),
    Column("decision_reason", Text),
    Column("created_at", Text, nullable=False),
    Column("decided_at", Text),
    UniqueConstraint(
        "run_id",
        "node_id",
        name="uq_workflow_reviews_run_node",
    ),
)
Index(
    "idx_workflow_reviews_run_status",
    workflow_review_requests.c.run_id,
    workflow_review_requests.c.status,
    workflow_review_requests.c.created_at,
)

workflow_audit_events = Table(
    "workflow_audit_events",
    metadata,
    Column("audit_id", Text, primary_key=True),
    Column(
        "app_id",
        Text,
        ForeignKey("workflow_applications.app_id"),
        nullable=False,
    ),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text, nullable=False),
    Column("details_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
Index(
    "idx_workflow_audit_app_created",
    workflow_audit_events.c.app_id,
    desc(workflow_audit_events.c.created_at),
)

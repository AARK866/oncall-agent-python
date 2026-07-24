import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def use_local_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "log_level", "INFO")
    monkeypatch.setattr(settings, "log_format", "json")
    monkeypatch.setattr(settings, "log_file_path", None)
    monkeypatch.setattr(settings, "log_file_max_bytes", 10_485_760)
    monkeypatch.setattr(settings, "log_file_backup_count", 5)
    monkeypatch.setattr(
        settings,
        "telemetry_service_name",
        "oncall-agent",
    )
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_auth_token", None)
    monkeypatch.setattr(settings, "audit_enabled", True)
    monkeypatch.setattr(settings, "audit_persist_enabled", False)
    monkeypatch.setattr(settings, "audit_retention_days", 180)
    monkeypatch.setattr(
        settings,
        "audit_cleanup_interval_seconds",
        86400,
    )
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(settings, "api_token_subject", "api-client")
    monkeypatch.setattr(settings, "api_token_roles", "oncall,sre")
    monkeypatch.setattr(settings, "auth_mode", "api-token")
    monkeypatch.setattr(settings, "default_tenant_id", "default")
    monkeypatch.setattr(settings, "jwt_issuer", None)
    monkeypatch.setattr(settings, "jwt_audience", None)
    monkeypatch.setattr(settings, "jwt_jwks_url", None)
    monkeypatch.setattr(settings, "jwt_secret", None)
    monkeypatch.setattr(settings, "jwt_algorithms", "RS256")
    monkeypatch.setattr(settings, "jwt_tenant_claim", "tenant_id")
    monkeypatch.setattr(settings, "jwt_roles_claim", "roles")
    monkeypatch.setattr(settings, "jwt_permissions_claim", "permissions")
    monkeypatch.setattr(settings, "jwt_clock_skew_seconds", 30)
    monkeypatch.setattr(settings, "webhook_secret", None)
    monkeypatch.setattr(settings, "require_auth_in_production", True)
    monkeypatch.setattr(settings, "database_url", None)
    monkeypatch.setattr(settings, "database_pool_size", 5)
    monkeypatch.setattr(settings, "database_max_overflow", 5)
    monkeypatch.setattr(settings, "database_pool_timeout_seconds", 5)
    monkeypatch.setattr(settings, "database_pool_recycle_seconds", 300)
    monkeypatch.setattr(settings, "database_auto_create_schema", True)
    monkeypatch.setattr(settings, "task_queue_mode", "local")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/15")
    monkeypatch.setattr(settings, "celery_result_backend", None)
    monkeypatch.setattr(settings, "celery_task_always_eager", False)
    monkeypatch.setattr(settings, "celery_task_eager_propagates", True)
    monkeypatch.setattr(settings, "celery_result_expires_seconds", 3600)
    monkeypatch.setattr(settings, "task_dispatch_dedupe_ttl_seconds", 30)
    monkeypatch.setattr(settings, "task_execution_lock_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "task_broker_publish_max_retries", 3)
    monkeypatch.setattr(
        settings,
        "task_broker_publish_retry_delay_seconds",
        0.01,
    )
    monkeypatch.setattr(settings, "stale_task_recovery_interval_seconds", 60)
    monkeypatch.setattr(settings, "stale_task_auto_resume_enabled", True)
    monkeypatch.setattr(settings, "redis_key_prefix", "oncall-agent-test")
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    monkeypatch.setattr(settings, "embedding_api_key", None)
    monkeypatch.setattr(settings, "knowledge_engine", "local")
    monkeypatch.setattr(settings, "knowledge_reranker", "none")
    monkeypatch.setattr(settings, "knowledge_rerank_candidate_multiplier", 3)
    monkeypatch.setattr(settings, "knowledge_rerank_vector_weight", 0.7)
    monkeypatch.setattr(settings, "knowledge_rerank_lexical_weight", 0.3)
    monkeypatch.setattr(settings, "knowledge_retriever_mode", "keyword")
    monkeypatch.setattr(settings, "knowledge_vector_store", "in_memory")
    monkeypatch.setattr(settings, "knowledge_allowed_extensions", ".md,.txt,.pdf,.docx")
    monkeypatch.setattr(settings, "knowledge_default_access_scope", "internal")
    monkeypatch.setattr(settings, "knowledge_default_allowed_roles", "oncall,sre")
    monkeypatch.setattr(settings, "knowledge_acl_enabled", True)
    monkeypatch.setattr(settings, "knowledge_system_subject", "oncall-agent")
    monkeypatch.setattr(settings, "knowledge_system_roles", "oncall,sre")
    monkeypatch.setattr(settings, "knowledge_incremental_indexing_enabled", False)
    monkeypatch.setattr(settings, "knowledge_manifest_db_path", "app/data/knowledge_manifest.db")
    monkeypatch.setattr(
        settings,
        "knowledge_ingestion_task_db_path",
        "app/data/knowledge_ingestion_tasks.db",
    )
    monkeypatch.setattr(settings, "knowledge_ingestion_max_attempts", 3)
    monkeypatch.setattr(settings, "workflow_db_path", "app/data/workflows.db")
    monkeypatch.setattr(settings, "workflow_checkpointer", "memory")
    monkeypatch.setattr(
        settings,
        "workflow_checkpoint_db_path",
        "app/data/workflow_langgraph_checkpoints.sqlite",
    )
    monkeypatch.setattr(settings, "ops_tool_mode", "mock")
    monkeypatch.setattr(settings, "prometheus_base_url", None)
    monkeypatch.setattr(settings, "prometheus_bearer_token", None)
    monkeypatch.setattr(settings, "prometheus_username", None)
    monkeypatch.setattr(settings, "prometheus_password", None)
    monkeypatch.setattr(settings, "prometheus_verify_ssl", True)
    monkeypatch.setattr(settings, "loki_base_url", None)
    monkeypatch.setattr(settings, "loki_bearer_token", None)
    monkeypatch.setattr(settings, "loki_username", None)
    monkeypatch.setattr(settings, "loki_password", None)
    monkeypatch.setattr(settings, "loki_org_id", None)
    monkeypatch.setattr(settings, "loki_verify_ssl", True)
    monkeypatch.setattr(settings, "gitlab_base_url", None)
    monkeypatch.setattr(settings, "gitlab_token", None)
    monkeypatch.setattr(settings, "gitlab_project_id", None)
    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setattr(settings, "github_repo", None)
    monkeypatch.setattr(settings, "github_branch", "main")
    monkeypatch.setattr(settings, "github_verify_ssl", True)
    monkeypatch.setattr(settings, "github_proxy_url", None)
    monkeypatch.setattr(settings, "github_allowed_paths", "")
    monkeypatch.setattr(settings, "github_max_file_bytes", 2_000_000)
    monkeypatch.setattr(settings, "github_max_patch_chars", 4000)
    monkeypatch.setattr(settings, "ops_http_max_connections", 20)
    monkeypatch.setattr(
        settings,
        "ops_http_max_keepalive_connections",
        10,
    )
    monkeypatch.setattr(settings, "diagnosis_task_timeout_seconds", 900)
    monkeypatch.setattr(settings, "diagnosis_task_recovery_limit", 50)
    monkeypatch.setattr(settings, "ops_graph_checkpointer", "memory")

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def use_local_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(settings, "api_token_subject", "api-client")
    monkeypatch.setattr(settings, "api_token_roles", "oncall,sre")
    monkeypatch.setattr(settings, "webhook_secret", None)
    monkeypatch.setattr(settings, "require_auth_in_production", True)
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
    monkeypatch.setattr(settings, "loki_base_url", None)
    monkeypatch.setattr(settings, "gitlab_base_url", None)
    monkeypatch.setattr(settings, "gitlab_token", None)
    monkeypatch.setattr(settings, "gitlab_project_id", None)
    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setattr(settings, "github_repo", None)
    monkeypatch.setattr(settings, "github_branch", "main")
    monkeypatch.setattr(settings, "diagnosis_task_timeout_seconds", 900)
    monkeypatch.setattr(settings, "diagnosis_task_recovery_limit", 50)
    monkeypatch.setattr(settings, "ops_graph_checkpointer", "memory")

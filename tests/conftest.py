import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def use_local_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(settings, "webhook_secret", None)
    monkeypatch.setattr(settings, "require_auth_in_production", True)
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    monkeypatch.setattr(settings, "embedding_api_key", None)
    monkeypatch.setattr(settings, "knowledge_retriever_mode", "keyword")
    monkeypatch.setattr(settings, "knowledge_vector_store", "in_memory")
    monkeypatch.setattr(settings, "ops_tool_mode", "mock")
    monkeypatch.setattr(settings, "prometheus_base_url", None)
    monkeypatch.setattr(settings, "loki_base_url", None)
    monkeypatch.setattr(settings, "gitlab_base_url", None)
    monkeypatch.setattr(settings, "gitlab_token", None)
    monkeypatch.setattr(settings, "gitlab_project_id", None)
    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setattr(settings, "github_repo", None)
    monkeypatch.setattr(settings, "github_branch", "main")

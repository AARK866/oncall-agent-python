from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OnCall Agent Python"
    app_env: str = "local"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    llm_provider: str = "mock"
    llm_model: str = "mock-oncall-agent"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: int = 30
    incident_db_path: str = "app/data/oncall_agent.db"
    ops_tool_mode: str = "mock"
    prometheus_base_url: str | None = None
    prometheus_timeout_seconds: int = 10
    loki_base_url: str | None = None
    loki_timeout_seconds: int = 10
    gitlab_base_url: str | None = None
    gitlab_token: str | None = None
    gitlab_project_id: str | None = None
    gitlab_timeout_seconds: int = 10
    ops_graph_runtime: str = "local"
    knowledge_retriever_mode: str = "keyword"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OnCall Agent Python"
    app_env: str = "local"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    api_auth_enabled: bool = False
    api_token: str | None = None
    webhook_secret: str | None = None
    require_auth_in_production: bool = True
    llm_provider: str = "mock"
    llm_model: str = "mock-oncall-agent"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 6
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str | None = None
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dimensions: int = 1536
    embedding_request_dimensions: bool = False
    embedding_tiktoken_enabled: bool = False
    embedding_check_ctx_length: bool = False
    embedding_timeout_seconds: int = 30
    embedding_max_retries: int = 6
    knowledge_vector_store: str = "in_memory"
    knowledge_source: str = "local"
    knowledge_local_path: str = "app/data/runbooks"
    knowledge_github_path: str = "app/data/runbooks"
    knowledge_ingest_chunk_size: int = 800
    knowledge_ingest_chunk_overlap: int = 120
    milvus_uri: str | None = None
    milvus_token: str | None = None
    milvus_db_name: str | None = None
    milvus_collection_name: str = "oncall_runbook_chunks"
    milvus_vector_field: str = "vector"
    milvus_primary_field: str = "chunk_id"
    milvus_metric_type: str = "COSINE"
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
    github_token: str | None = None
    github_base_url: str = "https://api.github.com"
    github_repo: str | None = None
    github_branch: str = "main"
    github_timeout_seconds: int = 10
    ops_graph_runtime: str = "local"
    knowledge_retriever_mode: str = "keyword"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

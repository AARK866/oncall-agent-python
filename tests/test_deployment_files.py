from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_runs_uvicorn_and_exposes_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.10-slim" in dockerfile
    assert "python -m pip install -r requirements.txt" in dockerfile
    assert "uvicorn" in dockerfile
    assert "/health" in dockerfile
    assert "USER app" in dockerfile


def test_dockerignore_excludes_local_secrets_and_runtime_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    assert ".venv" in dockerignore
    assert "*.db" in dockerignore


def test_docker_compose_defines_api_and_optional_milvus_profile() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "api:" in compose
    assert "oncall-agent-api" in compose
    assert "8000:8000" in compose
    assert "profiles:" in compose
    assert "milvus" in compose
    assert "oncall-data" in compose
    assert "postgres:" in compose
    assert "DATABASE_URL:" in compose
    assert 'DATABASE_AUTO_CREATE_SCHEMA: "false"' in compose


def test_github_actions_runs_tests_and_docker_build() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v4" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "python -m pytest" in workflow
    assert "docker build" in workflow
    assert "scripts/check_enterprise_stack.py --config-only" in workflow

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_runs_uvicorn_and_exposes_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.10-slim" in dockerfile
    assert "python -m pip install -r requirements.txt" in dockerfile
    assert "uvicorn" in dockerfile
    assert "/health" in dockerfile
    assert "USER app" in dockerfile
    assert "CMD [\"python\", \"-m\", \"uvicorn\"" in dockerfile
    assert "CMD [\"sh\", \"-c\", \"python -m alembic" not in dockerfile


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
    assert "redis:" in compose
    assert "worker:" in compose
    assert "beat:" in compose
    assert "TASK_QUEUE_MODE: celery" in compose
    assert "service_completed_successfully" in compose
    assert "scripts/run_production_migrations.py" in compose
    assert "POSTGRES_APP_USER" in compose
    assert "POSTGRES_APP_PASSWORD" in compose
    assert "init-app-role.sh" in compose
    assert "payment-api:" in compose
    assert "services/payment_api/Dockerfile" in compose
    assert "8010:8010" in compose
    assert "payment-traffic-generator:" in compose
    assert "PAYMENT_API_REMEDIATION_ENABLED" in compose
    assert "PAYMENT_API_BASE_URL" in compose
    assert "PAYMENT_API_FAULT_ADMIN_TOKEN" in compose

    role_bootstrap = (
        ROOT / "docker" / "postgres" / "init-app-role.sh"
    ).read_text(encoding="utf-8")
    assert "NOSUPERUSER" in role_bootstrap
    assert "NOBYPASSRLS" in role_bootstrap


def test_github_actions_runs_tests_and_docker_build() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v4" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "python -m pytest" in workflow
    assert "docker build" in workflow
    assert "scripts/check_enterprise_stack.py --config-only" in workflow


def test_observability_deployment_files_define_scrape_alerts_and_dashboard() -> None:
    prometheus = (
        ROOT / "deploy" / "observability" / "prometheus.yml"
    ).read_text(encoding="utf-8")
    alerts = (
        ROOT / "deploy" / "observability" / "oncall-agent-alerts.yml"
    ).read_text(encoding="utf-8")
    dashboard = json.loads(
        (
            ROOT / "deploy" / "observability" / "grafana-dashboard.json"
        ).read_text(encoding="utf-8")
    )

    assert "/metrics" in prometheus
    assert "credentials_file" in prometheus
    assert "oncall-agent-alerts.yml" in prometheus
    assert "OnCallAgentDown" in alerts
    assert "OnCallAgentAuditWriteFailure" in alerts
    assert dashboard["uid"] == "oncall-agent-ops"
    assert dashboard["title"] == "OnCall Agent Operations"

    local_prometheus = (
        ROOT / "deploy" / "observability" / "prometheus-local.yml"
    ).read_text(encoding="utf-8")
    local_loki = (
        ROOT / "deploy" / "observability" / "loki-local.yml"
    ).read_text(encoding="utf-8")
    local_compose = (
        ROOT / "deploy" / "observability" / "docker-compose.yml"
    ).read_text(encoding="utf-8")
    payment_alerts = (
        ROOT
        / "deploy"
        / "observability"
        / "payment-api-alerts.yml"
    ).read_text(encoding="utf-8")
    alertmanager = (
        ROOT
        / "deploy"
        / "observability"
        / "alertmanager-local.yml"
    ).read_text(encoding="utf-8")

    assert "host.docker.internal:8000" in local_prometheus
    assert "host.docker.internal:8010" in local_prometheus
    assert "alertmanager:9093" in local_prometheus
    assert "payment-api-alerts.yml" in local_prometheus
    assert "service: oncall-agent" in local_prometheus
    assert "schema: v13" in local_loki
    assert "oncall-prometheus" in local_compose
    assert "oncall-loki" in local_compose
    assert "oncall-alertmanager" in local_compose
    assert "alertmanager_webhook_token" in local_compose
    assert "PaymentApiDown" in payment_alerts
    assert "PaymentApiHigh5xxRatio" in payment_alerts
    assert "PaymentApiHighP95Latency" in payment_alerts
    assert 'status=~"5.."' in payment_alerts
    assert "/api/alerts/alertmanager" in alertmanager
    assert "send_resolved: true" in alertmanager
    assert "credentials_file:" in alertmanager


def test_kubernetes_base_supports_safe_multi_replica_deployment() -> None:
    base = ROOT / "deploy" / "kubernetes" / "base"
    api = (base / "api-deployment.yaml").read_text(encoding="utf-8")
    worker = (base / "worker-deployment.yaml").read_text(encoding="utf-8")
    beat = (base / "beat-deployment.yaml").read_text(encoding="utf-8")
    config = (base / "configmap.yaml").read_text(encoding="utf-8")
    hpa = (base / "api-hpa.yaml").read_text(encoding="utf-8")
    pdb = (base / "api-pdb.yaml").read_text(encoding="utf-8")

    assert "replicas: 2" in api
    assert "maxUnavailable: 0" in api
    assert "readOnlyRootFilesystem: true" in api
    assert "runAsNonRoot: true" in api
    assert "startupProbe:" in api
    assert "readinessProbe:" in api
    assert "livenessProbe:" in api
    assert "replicas: 2" in worker
    assert "--queues=diagnosis,knowledge,maintenance" in worker
    assert "replicas: 1" in beat
    assert "type: Recreate" in beat
    assert "OPS_GRAPH_CHECKPOINTER: postgres" in config
    assert "WORKFLOW_CHECKPOINTER: postgres" in config
    assert "PAYMENT_API_REMEDIATION_ENABLED:" in config
    assert "PAYMENT_API_BASE_URL:" in config
    assert "minReplicas: 2" in hpa
    assert "maxReplicas: 10" in hpa
    assert "minAvailable: 1" in pdb


def test_kubernetes_migration_and_secret_templates_are_safe() -> None:
    deployment = ROOT / "deploy" / "kubernetes"
    migration = (deployment / "migration-job.yaml").read_text(
        encoding="utf-8"
    )
    secret_example = (deployment / "secret.example.yaml").read_text(
        encoding="utf-8"
    )
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "scripts/run_production_migrations.py" in migration
    assert "REPLACE_WITH" in secret_example
    assert "sk-" not in secret_example
    assert "ghp_" not in secret_example
    assert "PAYMENT_API_FAULT_ADMIN_TOKEN" in secret_example
    assert "deploy/kubernetes/secret.yaml" in gitignore

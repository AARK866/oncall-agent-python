import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.main import app


def main() -> int:
    original_values = {
        "database_url": settings.database_url,
        "incident_db_path": settings.incident_db_path,
        "database_auto_create_schema": (
            settings.database_auto_create_schema
        ),
        "audit_enabled": settings.audit_enabled,
        "audit_persist_enabled": settings.audit_persist_enabled,
        "metrics_enabled": settings.metrics_enabled,
        "metrics_auth_token": settings.metrics_auth_token,
        "api_auth_enabled": settings.api_auth_enabled,
        "auth_mode": settings.auth_mode,
    }
    try:
        with TemporaryDirectory() as directory:
            settings.database_url = None
            settings.incident_db_path = str(
                Path(directory) / "observability.db"
            )
            settings.database_auto_create_schema = True
            settings.audit_enabled = True
            settings.audit_persist_enabled = True
            settings.metrics_enabled = True
            settings.metrics_auth_token = "acceptance-token"
            settings.api_auth_enabled = False
            settings.auth_mode = "api-token"
            return _run_checks()
    finally:
        for name, value in original_values.items():
            setattr(settings, name, value)


def _run_checks() -> int:
    client = TestClient(app)
    trace_id = "observability-check-123"
    health = client.get(
        "/health",
        headers={"X-Trace-ID": trace_id},
    )
    tools = client.get(
        "/api/tools/health?mode=mock",
        headers={"X-Trace-ID": trace_id},
    )
    audit = client.get("/api/audit-events")
    metrics = client.get(
        "/metrics",
        headers={"Authorization": "Bearer acceptance-token"},
    )

    events = audit.json() if audit.status_code == 200 else []
    audit_match = any(
        event.get("trace_id") == trace_id
        and event.get("action") == "GET /api/tools/health"
        for event in events
    )
    checks = {
        "trace_header": (
            health.status_code == 200
            and health.headers.get("X-Trace-ID") == trace_id
        ),
        "audit_event": tools.status_code == 200 and audit_match,
        "metrics_auth": metrics.status_code == 200,
        "http_metrics": (
            "oncall_http_requests_total" in metrics.text
            and "oncall_http_request_duration_seconds" in metrics.text
        ),
    }

    print("Observability acceptance")
    for name, passed in checks.items():
        print(f"- {name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

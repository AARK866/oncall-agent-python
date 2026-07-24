# Enterprise Observability

The application now exposes one correlated request trail:

```text
HTTP request
  -> X-Trace-ID / traceparent
  -> JSON application log
  -> Prometheus request metric
  -> tenant-scoped audit event
  -> Agent tool and task dispatch metrics
```

## Configuration

```dotenv
LOG_LEVEL=INFO
LOG_FORMAT=json

METRICS_ENABLED=true
METRICS_AUTH_TOKEN=replace-with-a-long-random-token

AUDIT_ENABLED=true
AUDIT_PERSIST_ENABLED=true
AUDIT_RETENTION_DAYS=180
AUDIT_CLEANUP_INTERVAL_SECONDS=86400
```

Production validation requires persistent auditing. When metrics are enabled in
production, `METRICS_AUTH_TOKEN` is mandatory.

## Trace And Logs

The API accepts either W3C `traceparent` or a safe `X-Trace-ID`. Invalid or
missing IDs are replaced with a random 32-character ID. Every response includes
`X-Trace-ID`.

JSON logs include timestamp, level, logger, message, trace ID, tenant ID, actor,
event type, outcome, route, status, and duration when available. Configured
secrets are redacted.

Do not use tenant, user, trace, task, or incident IDs as Prometheus labels. They
belong in logs and audit records; high-cardinality metric labels make
Prometheus expensive and unstable.

## Metrics

Prometheus scrapes:

```text
GET /metrics
Authorization: Bearer <METRICS_AUTH_TOKEN>
```

Main metric families:

- `oncall_http_requests_total`
- `oncall_http_request_duration_seconds`
- `oncall_http_requests_in_progress`
- `oncall_tool_calls_total`
- `oncall_tool_call_duration_seconds`
- `oncall_task_dispatches_total`
- `oncall_audit_write_failures_total`

Reference scrape and alert files:

- `deploy/observability/prometheus.yml`
- `deploy/observability/oncall-agent-alerts.yml`
- `deploy/observability/grafana-dashboard.json`

The Prometheus credentials file must contain exactly the value configured in
`METRICS_AUTH_TOKEN`.

## Audit

`audit_events` stores actor, tenant, action, route, outcome, trace ID, status,
duration, and minimal request metadata. Request bodies, JWTs, API keys, and
repository tokens are never stored.

SRE and admin roles can query their current tenant:

```text
GET /api/audit-events?limit=100
GET /api/audit-events?event_type=api.request&outcome=denied
```

PostgreSQL RLS enforces tenant isolation. Celery Beat removes expired events
using `AUDIT_RETENTION_DAYS`.

## Acceptance

Apply the migration and run:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\check_observability.py
```

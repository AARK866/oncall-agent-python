# Security And Production Configuration

This step adds lightweight production security controls.

## Environment Variables

```text
API_AUTH_ENABLED=true
API_TOKEN=replace-with-a-long-random-token
WEBHOOK_SECRET=replace-with-a-long-random-secret
REQUIRE_AUTH_IN_PRODUCTION=true
```

Development defaults keep authentication disabled so local learning remains convenient.
In production, `APP_ENV=production` makes API authentication required when
`REQUIRE_AUTH_IN_PRODUCTION=true`.

## API Token

Protected endpoints accept either:

```http
X-API-Key: your-token
```

or:

```http
Authorization: Bearer your-token
```

Protected areas:

- `POST /api/alerts/analyze`
- `GET /api/alerts/groups`
- `GET /api/alerts/groups/{group_id}`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events`
- `POST /api/knowledge/ingest`

## Alertmanager Webhook Signature

`POST /api/alerts/alertmanager` uses HMAC SHA-256 when `WEBHOOK_SECRET` is configured.

Header:

```http
X-OnCall-Signature: sha256=<hex-digest>
```

The digest is computed from the raw HTTP request body:

```python
import hashlib
import hmac

signature = "sha256=" + hmac.new(
    WEBHOOK_SECRET.encode("utf-8"),
    raw_body,
    hashlib.sha256,
).hexdigest()
```

If `WEBHOOK_SECRET` is empty, the webhook endpoint falls back to API token protection
when API authentication is enabled.

Native Alertmanager can instead use a dedicated Bearer credential:

```env
ALERTMANAGER_WEBHOOK_TOKEN=replace-with-a-long-random-token
```

This token is checked before HMAC validation and is redacted from application
logs. Production validation accepts either `WEBHOOK_SECRET` or
`ALERTMANAGER_WEBHOOK_TOKEN`.

## Approved payment remediation

`PAYMENT_API_REMEDIATION_ENABLED` defaults to `false`. When enabled, production
validation also requires `PAYMENT_API_BASE_URL` and
`PAYMENT_API_FAULT_ADMIN_TOKEN`.

The reset action is not registered in the LLM-visible diagnostic tool pool. It
is executed only by the post-approval LangGraph node and only for allowlisted
payment-api alerts. The administration token is redacted from application
logs.

## Production Check

Run:

```powershell
.\.venv\Scripts\python.exe scripts\check_enterprise_stack.py --config-only
```

For `APP_ENV=production`, this check reports missing `API_TOKEN` and `WEBHOOK_SECRET`.
Error output is redacted so configured secrets are not printed directly.

## Signed Local Check

```powershell
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py --in-process --mock-llm --webhook-secret test-secret
```

The script signs the raw JSON body with `X-OnCall-Signature`, then polls the returned
task until the diagnosis finishes.

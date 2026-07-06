# Alert Webhook

This step adds an alert ingestion layer in front of the existing OpsAgent.

## Endpoints

### POST `/api/alerts/analyze`

Use this endpoint when another service has already normalized the alert.

```json
{
  "alert_id": "alert-payment-5xx",
  "title": "High5xxRate",
  "service": "payment-api",
  "severity": "critical",
  "labels": {
    "team": "payments"
  },
  "annotations": {
    "summary": "payment-api 5xx is above threshold"
  }
}
```

The API converts the alert into an ops question, runs `OpsAgent`, stores the incident,
and returns the diagnosis result.

### POST `/api/alerts/alertmanager`

Use this endpoint as a Prometheus Alertmanager webhook receiver.

```json
{
  "version": "4",
  "status": "firing",
  "receiver": "oncall-agent",
  "commonLabels": {
    "alertname": "High5xxRate",
    "service": "payment-api",
    "severity": "critical"
  },
  "commonAnnotations": {
    "summary": "payment-api has elevated 5xx responses"
  },
  "alerts": [
    {
      "status": "firing",
      "startsAt": "2026-07-06T10:00:00Z",
      "fingerprint": "payment-5xx-fingerprint"
    }
  ]
}
```

Only `firing` alerts are processed. `resolved` alerts are counted as ignored.

## Runtime Flow

```mermaid
flowchart LR
    A["Alertmanager webhook"] --> B["/api/alerts/alertmanager"]
    B --> C["Filter firing alerts"]
    C --> D["Build diagnosis question"]
    D --> E["OpsAgent graph"]
    E --> F["Real or mock ops tools"]
    E --> G["Knowledge retrieval"]
    E --> H["LLM summary"]
    H --> I["SQLite incident history"]
    I --> J["API response"]
```

## Local Check

Run a mock local check without starting the server:

```powershell
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py --in-process --mock-llm
```

Run the same alert through real Prometheus, Loki, GitHub, Milvus, and embedding settings:

```powershell
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py --in-process --mock-llm --real-tools
```

If you are running the FastAPI server manually, use:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py
```

## Alertmanager Config Example

```yaml
receivers:
  - name: oncall-agent
    webhook_configs:
      - url: http://host.docker.internal:8000/api/alerts/alertmanager
        send_resolved: true
```

For Linux Docker networking, replace `host.docker.internal` with the API host or container
service name used by your deployment.

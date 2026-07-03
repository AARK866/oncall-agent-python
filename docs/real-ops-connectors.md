# Real Ops Connectors

The project now has a real connector layer for future production systems:

- Prometheus: metrics
- Loki: logs
- GitLab: deployments
- Topology: placeholder for CMDB, Kubernetes, or service graph

Default mode is still mock:

```env
OPS_TOOL_MODE=mock
```

## Switch To Real Mode

Edit `.env`:

```env
OPS_TOOL_MODE=real
PROMETHEUS_BASE_URL=http://localhost:9090
LOKI_BASE_URL=http://localhost:3100
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_TOKEN=replace-with-token
GITLAB_PROJECT_ID=123
```

Then start the API:

```powershell
python -m uvicorn app.main:app --reload
```

Run an incident analysis:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/incidents/analyze `
  -ContentType "application/json" `
  -Body '{"message":"payment service 5xx error rate is high","session_id":"real-tools-test","mode":"ops"}'
```

Check response metadata:

```text
metadata.tool_connector.mode = real
metadata.tool_results
```

If a required URL or token is missing, the tool result will show a clear configuration error.

## Current Tool Mapping

| Agent tool | Real backend |
| --- | --- |
| `query_metrics` | Prometheus `/api/v1/query` |
| `query_logs` | Loki `/loki/api/v1/query_range` |
| `query_deployments` | GitLab `/api/v4/projects/{project_id}/deployments` |
| `query_service_topology` | Placeholder for CMDB/Kubernetes/service graph |

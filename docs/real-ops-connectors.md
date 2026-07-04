# Real Ops Connectors

The project now has a real connector layer for future production systems:

- Prometheus: metrics
- Loki: logs
- GitLab: deployments
- GitHub: repository files and commits
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
GITHUB_BASE_URL=https://api.github.com
GITHUB_TOKEN=replace-with-token
GITHUB_REPO=AARK866/oncall-agent-python
GITHUB_BRANCH=main
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

## Real Incident Flow Check

Run one full OpsAgent diagnosis with real tools:

```powershell
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
.\.venv\Scripts\python.exe scripts\check_real_incident_flow.py --mock-llm
```

`--mock-llm` keeps Prometheus, Loki, Milvus, and GitHub real, but avoids blocking
the infrastructure check on external LLM connectivity. Remove it when the LLM
proxy is configured for Python processes.

## Current Tool Mapping

| Agent tool | Real backend |
| --- | --- |
| `query_metrics` | Prometheus `/api/v1/query` |
| `query_logs` | Loki `/loki/api/v1/query_range` |
| `query_deployments` | GitLab `/api/v4/projects/{project_id}/deployments` |
| `query_recent_commits` | GitHub `/repos/{owner}/{repo}/commits` |
| `query_commit_detail` | GitHub `/repos/{owner}/{repo}/commits/{sha}` |
| `read_repository_file` | GitHub `/repos/{owner}/{repo}/contents/{path}` |
| `query_service_topology` | Placeholder for CMDB/Kubernetes/service graph |

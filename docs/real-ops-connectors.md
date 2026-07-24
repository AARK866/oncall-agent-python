# Real Ops Connectors

The project now has a real connector layer for future production systems:

- Prometheus: metrics
- Loki: logs
- GitLab: deployments
- GitHub: repository files and commits
- Topology: placeholder for CMDB, Kubernetes, or service graph

Local tests default to mock. Production validation requires real mode:

```env
OPS_TOOL_MODE=real
```

## Switch To Real Mode

Edit `.env`:

```env
OPS_TOOL_MODE=real
PROMETHEUS_BASE_URL=http://localhost:9090
PROMETHEUS_BEARER_TOKEN=
PROMETHEUS_USERNAME=
PROMETHEUS_PASSWORD=
PROMETHEUS_VERIFY_SSL=true

LOKI_BASE_URL=http://localhost:3100
LOKI_BEARER_TOKEN=
LOKI_USERNAME=
LOKI_PASSWORD=
LOKI_ORG_ID=
LOKI_VERIFY_SSL=true

GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_TOKEN=replace-with-token
GITLAB_PROJECT_ID=123

GITHUB_BASE_URL=https://api.github.com
GITHUB_TOKEN=replace-with-token
GITHUB_REPO=AARK866/oncall-agent-python
GITHUB_BRANCH=main
GITHUB_VERIFY_SSL=true
GITHUB_PROXY_URL=http://127.0.0.1:7897
GITHUB_ALLOWED_PATHS=app,docs
GITHUB_MAX_FILE_BYTES=2000000
GITHUB_MAX_PATCH_CHARS=4000

OPS_HTTP_MAX_CONNECTIONS=20
OPS_HTTP_MAX_KEEPALIVE_CONNECTIONS=10
```

Prometheus and Loki support either bearer-token authentication or HTTP Basic
authentication. `LOKI_ORG_ID` becomes the `X-Scope-OrgID` header used by
multi-tenant Loki deployments. Leave `GITHUB_ALLOWED_PATHS` empty to permit the
whole configured repository, or set comma-separated path prefixes.

The Agent never receives a free-form PromQL or LogQL execution tool. It supplies
a validated service name and a bounded `1m` to `24h` time window; the Harness
builds controlled query templates. Log result limits, GitHub paths, file sizes,
commit patches, refs, and SHAs are also bounded before network execution.

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

## Real API Flow Check

Check the FastAPI incident endpoint in-process:

```powershell
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
.\.venv\Scripts\python.exe scripts\check_real_api_flow.py --in-process --mock-llm
```

Check a running API server:

```powershell
$env:OPS_TOOL_MODE='real'
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Then in another terminal:

```powershell
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
.\.venv\Scripts\python.exe scripts\check_real_api_flow.py
```

## Current Tool Mapping

| Agent tool | Real backend |
| --- | --- |
| `query_metrics` | Prometheus `/api/v1/query` |
| `query_logs` | Loki `/loki/api/v1/query_range` |
| `query_deployments` | GitLab when configured, otherwise GitHub Deployments |
| `query_recent_commits` | GitHub `/repos/{owner}/{repo}/commits` |
| `query_commit_detail` | GitHub `/repos/{owner}/{repo}/commits/{sha}` |
| `read_repository_file` | GitHub `/repos/{owner}/{repo}/contents/{path}` |
| `query_service_topology` | Placeholder for CMDB/Kubernetes/service graph |

## Acceptance

The enterprise checker uses the same authenticated clients as the Agent:

```powershell
.\.venv\Scripts\python.exe scripts\check_enterprise_stack.py `
  --skip-llm --skip-embedding --skip-milvus
```

It executes a safe `up` query against Prometheus, checks Loki readiness plus a
bounded log query with the configured auth headers, and reads the latest GitHub
commit. Secrets are redacted from failures.

# Tools Health API

The tools health API shows which ops connector is active and whether backend configuration is complete.

Start the API:

```powershell
python -m uvicorn app.main:app --reload
```

## Current Mode

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tools/health
```

With the default `.env`, this returns mock mode:

```text
mode = mock
connector_name = mock_ops
ready = true
```

## Check Real Mode Without Switching `.env`

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/tools/health?mode=real"
```

This checks whether real backend settings are present:

- `PROMETHEUS_BASE_URL`
- `LOKI_BASE_URL`
- `GITLAB_BASE_URL`
- `GITLAB_PROJECT_ID`
- `GITHUB_REPO`

`GITLAB_TOKEN` is optional in the health response because some GitLab deployments may expose read-only deployment data differently.
`GITHUB_TOKEN` is optional for public repositories, but should be set for private repositories and higher rate limits.

## What It Does Not Do

This endpoint does not call Prometheus, Loki, GitLab, or GitHub over the network. It only checks local configuration and registered tools.

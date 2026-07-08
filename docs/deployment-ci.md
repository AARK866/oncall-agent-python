# Deployment And CI/CD

This step adds a deployable container shape and a GitHub Actions pipeline.

## Docker Image

Build locally:

```powershell
docker build -t oncall-agent-python:local .
```

Run locally:

```powershell
docker run --rm -p 8000:8000 --env-file .env oncall-agent-python:local
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Docker Compose

Copy and edit environment variables first:

```powershell
Copy-Item .env.example .env
```

Start the API:

```powershell
docker compose up --build api
```

Start API with local Milvus dependencies:

```powershell
docker compose --profile milvus up --build
```

When the API container talks to services running on your Windows host, use:

```text
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
PROMETHEUS_BASE_URL=http://host.docker.internal:9090
LOKI_BASE_URL=http://host.docker.internal:3100
```

When using the Compose Milvus profile, use:

```text
MILVUS_URI=http://milvus:19530
```

## Production Variables

At minimum, production should set:

```text
APP_ENV=production
API_AUTH_ENABLED=true
API_TOKEN=replace-with-a-long-random-token
WEBHOOK_SECRET=replace-with-a-long-random-secret
REQUIRE_AUTH_IN_PRODUCTION=true
INCIDENT_DB_PATH=/app/app/data/oncall_agent.db
```

Then add real integration settings for LLM, embedding, vector store, Prometheus, Loki,
GitHub, and GitLab as needed.

## GitHub Actions

The workflow lives at:

```text
.github/workflows/ci.yml
```

It runs on:

- push to `main`
- pull request to `main`
- manual `workflow_dispatch`

Jobs:

- install Python dependencies
- run `scripts/check_enterprise_stack.py --config-only`
- run `python -m pytest`
- build the Docker image

## Release Path

A simple production path is:

1. Push to GitHub.
2. Wait for CI to pass.
3. Build and tag the Docker image.
4. Push the image to a registry.
5. Deploy with Compose, Kubernetes, or a cloud container service.
6. Run `scripts/ingest_knowledge.py` after runbook changes.

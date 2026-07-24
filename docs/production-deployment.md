# Production deployment and release acceptance

This runbook deploys the API, Celery workers, and Celery Beat to Kubernetes.
PostgreSQL, Redis, Milvus, the embedding service, Prometheus, and Loki should
be managed services or separately operated platform components.

## 1. Release prerequisites

- A Kubernetes cluster with Metrics Server for HPA.
- A PostgreSQL database reachable by the application role.
- A Redis deployment with persistence and authentication.
- Milvus and a working embedding endpoint.
- Prometheus and Loki endpoints.
- An immutable container image tag, never `latest`.
- Application secrets stored in a secret manager in real production.

Validate the local release configuration before building:

```powershell
.\.venv\Scripts\python.exe scripts\check_enterprise_stack.py --config-only
.\.venv\Scripts\python.exe -m pytest
```

## 2. Build and publish the image

```powershell
$tag = "0.1.0"
docker build -t "ghcr.io/YOUR_ORG/oncall-agent-python:$tag" .
docker push "ghcr.io/YOUR_ORG/oncall-agent-python:$tag"
```

Set the same immutable image in:

- `deploy/kubernetes/base/kustomization.yaml`
- `deploy/kubernetes/migration-job.yaml`

Change environment-specific URLs and `GITHUB_REPO` in
`deploy/kubernetes/base/configmap.yaml`.

## 3. Create production secrets

Use `deploy/kubernetes/secret.example.yaml` only as a field reference.
Create `deploy/kubernetes/secret.yaml` locally or generate the Secret from
your secret manager. The real file is ignored by Git.

```powershell
kubectl apply -f deploy/kubernetes/base/namespace.yaml
kubectl apply -f deploy/kubernetes/secret.yaml
```

Use `AUTH_MODE=jwt` with an enterprise identity provider when available.
The base manifest uses API token mode so the stack can be deployed before
an identity provider is connected.

## 4. Run database migration

The image no longer changes the database during API startup. The migration
Job runs both Alembic and LangGraph PostgreSQL Checkpointer initialization.
This prevents every API replica from racing to create or migrate tables.

```powershell
kubectl apply -k deploy/kubernetes/base
kubectl delete job oncall-agent-migrate -n oncall-agent --ignore-not-found
kubectl apply -f deploy/kubernetes/migration-job.yaml
kubectl wait --for=condition=complete job/oncall-agent-migrate `
  -n oncall-agent --timeout=300s
kubectl logs job/oncall-agent-migrate -n oncall-agent
```

The API readiness probe remains unavailable until the migration completes.

## 5. Verify rollout

```powershell
kubectl rollout status deployment/oncall-agent-api `
  -n oncall-agent --timeout=300s
kubectl rollout status deployment/oncall-agent-worker `
  -n oncall-agent --timeout=300s
kubectl get pods,hpa,pdb -n oncall-agent
kubectl port-forward service/oncall-agent-api 8000:80 -n oncall-agent
```

In another PowerShell terminal, run the final gate:

```powershell
.\.venv\Scripts\python.exe scripts\run_release_gate.py `
  --target-url http://127.0.0.1:8000 `
  --validate-production-config `
  --require-auth `
  --load-requests 500 `
  --load-concurrency 25 `
  --max-error-rate 0.01 `
  --max-p95-ms 500
```

The release is accepted only when every check reports `PASS`.

## 6. Load test business endpoints

The release gate tests `/health`, which measures API overhead without paying
for LLM calls. Use a JSON body file for a controlled staging Agent test:

```powershell
$env:LOAD_TEST_API_TOKEN = $env:API_TOKEN
.\.venv\Scripts\python.exe scripts\run_load_test.py `
  --target-url http://127.0.0.1:8000 `
  --path /api/chat `
  --method POST `
  --body-file deploy/load/chat-request.json `
  --requests 50 `
  --concurrency 5 `
  --max-error-rate 0.02 `
  --max-p95-ms 30000
```

Start with low concurrency because this endpoint consumes LLM, embedding,
vector database, and operations API quotas.

## 7. Failure drills

Preview the Docker Compose drill without changing services:

```powershell
.\.venv\Scripts\python.exe scripts\run_failure_drills.py
```

Execute only in a disposable environment:

```powershell
.\.venv\Scripts\python.exe scripts\run_failure_drills.py `
  --scenario all `
  --execute
```

The drill always starts the stopped service in a `finally` recovery path.
Expected behavior:

- Redis outage: `/health` stays healthy and `/health/queue` becomes unhealthy.
- Worker outage: API liveness stays healthy.
- PostgreSQL outage: `/health/database` becomes unhealthy.
- Recovery: `/health/database` returns healthy within the timeout.

## 8. Rollback

Use the previous immutable image tag:

```powershell
kubectl rollout undo deployment/oncall-agent-api -n oncall-agent
kubectl rollout undo deployment/oncall-agent-worker -n oncall-agent
kubectl rollout status deployment/oncall-agent-api -n oncall-agent
```

Database downgrade is not part of automatic rollback. Prefer backward
compatible migrations and complete destructive schema cleanup in a later
release.

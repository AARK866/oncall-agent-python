# Redis and Celery Distributed Tasks

The production task runtime uses Redis as the Celery broker and result backend.
FastAPI persists a durable task record in PostgreSQL, publishes only the task
identifier, and returns HTTP 202. Separate Celery workers load the durable
record and perform diagnosis or knowledge ingestion.

PostgreSQL remains the source of truth for business task state. Redis contains
short-lived delivery coordination, execution leases, Celery messages, and
expiring task results.

## Configuration

Add these values to `.env` for a production-style runtime:

```dotenv
TASK_QUEUE_MODE=celery
REDIS_URL=redis://localhost:6380/0
CELERY_RESULT_BACKEND=redis://localhost:6380/1
CELERY_RESULT_EXPIRES_SECONDS=3600
TASK_DISPATCH_DEDUPE_TTL_SECONDS=30
TASK_EXECUTION_LOCK_TTL_SECONDS=3600
TASK_BROKER_PUBLISH_MAX_RETRIES=3
TASK_BROKER_PUBLISH_RETRY_DELAY_SECONDS=0.2
STALE_TASK_RECOVERY_INTERVAL_SECONDS=60
STALE_TASK_AUTO_RESUME_ENABLED=true
REDIS_KEY_PREFIX=oncall-agent
```

`TASK_QUEUE_MODE=local` keeps FastAPI `BackgroundTasks` behavior for isolated
tests. Production configuration validation requires `celery`.

Use `rediss://` with TLS and authentication for a managed production Redis
service. Do not expose an unauthenticated Redis port to the public network.
Compose publishes Redis on host port `6380` by default because `6379` is often
already occupied. Containers still connect to `redis:6379`. Override the host
port with `REDIS_PORT` when needed.

## Runtime roles

- `api`: validates the request, writes a PostgreSQL task record, and publishes
  the task identifier.
- `worker`: consumes `diagnosis`, `knowledge`, and `maintenance` queues.
- `beat`: periodically detects stale diagnosis tasks and resumes recoverable
  work from the latest checkpoint.
- `redis`: transports Celery messages, stores short-lived deduplication keys
  and execution leases, and keeps expiring Celery results.
- `postgres`: stores durable business state, attempts, events, checkpoints,
  reviews, and final results.
- `migrate`: applies Alembic migrations before API and worker startup.

## Reliability behavior

### Dispatch deduplication

Before publishing, the dispatcher writes a short-lived Redis key with `SET NX`.
Repeated publication of the same business task during the deduplication window
is suppressed. If broker publication fails, the reservation is released so the
API can retry safely. FastAPI returns HTTP 503 with a generic broker-unavailable
message; the durable PostgreSQL task record remains queued.

### Execution lease

Workers acquire a Redis lease for `(task kind, task id)`. Only one worker can
own the lease. Lease release uses a compare-and-delete Lua script so one worker
cannot delete another worker's lock.

Diagnosis tasks also use a PostgreSQL conditional status update. This second
guard prevents duplicate execution even if a Redis lease expires unexpectedly.

### Delivery and retries

Celery uses late acknowledgement, rejection on worker loss, prefetch `1`, and
bounded broker publication retries. Knowledge ingestion failures are requeued
up to `KNOWLEDGE_INGESTION_MAX_ATTEMPTS`.

Diagnosis failures are not blindly replayed because Agent tools may have side
effects. Worker-loss recovery marks stale work as timed out, creates a resume
task, and continues from the latest durable checkpoint.

## Start the distributed runtime

Start the full stack:

```powershell
docker compose up -d --build postgres redis migrate worker beat api
docker compose ps
```

The Compose worker defaults to concurrency `2`. Override it with:

```dotenv
CELERY_WORKER_CONCURRENCY=4
```

Scale workers when using Compose implementations that support service scaling:

```powershell
docker compose up -d --scale worker=3
```

For production orchestration, run API, worker, and beat as separate Kubernetes
Deployments. Run exactly one beat scheduler unless a distributed scheduler
with leader election is used.

## Verification

Check broker readiness:

```text
GET /health/queue
```

Run a real Redis and Celery worker acceptance probe:

```powershell
.\.venv\Scripts\python.exe scripts\check_distributed_queue.py
```

The probe verifies Redis connectivity, dispatch deduplication, execution lease
exclusivity, a real Celery worker round trip, and queue depth collection. It
does not run an LLM request or mutate business records.

## Production checklist

- Use Redis replication or a managed high-availability service.
- Configure TLS, authentication, network policies, and credential rotation.
- Keep the Redis eviction policy at `noeviction` for broker queues.
- Alert on queue depth, oldest message age, task failure rate, worker heartbeat,
  Redis memory pressure, rejected connections, and stale-task recovery count.
- Route diagnosis and knowledge ingestion to separate worker pools when their
  CPU, memory, or latency profiles diverge.

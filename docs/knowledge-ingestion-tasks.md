# Knowledge ingestion tasks

Large knowledge imports can run through a persistent background task instead of
holding an HTTP request open while files are parsed, embedded, and written to
Milvus.

## State model

```text
queued -> running -> succeeded
                  -> failed -> queued (retry)
```

Each SQLite task record contains the original ingestion request, current stage,
progress percentage, attempt count, final response, error, and timestamps. A
conditional SQLite update ensures that only one worker can claim a queued task.

The default retry limit is three attempts:

```env
KNOWLEDGE_INGESTION_TASK_DB_PATH=app/data/knowledge_ingestion_tasks.db
KNOWLEDGE_INGESTION_MAX_ATTEMPTS=3
```

## API workflow

Submit an ingestion task:

```powershell
$body = @{
  source = "local"
  path = "app/data/runbooks"
  chunk_size = 800
  chunk_overlap = 120
  full_rebuild = $false
} | ConvertTo-Json

$task = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/knowledge/ingestion-tasks `
  -ContentType "application/json" `
  -Body $body
```

Query progress:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/knowledge/ingestion-tasks/$($task.task_id)"
```

Retry a failed task:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/knowledge/ingestion-tasks/$($task.task_id)/retry" `
  -ContentType "application/json" `
  -Body '{"requested_by":"oncall"}'
```

The existing `POST /api/knowledge/ingest` endpoint remains available for short,
synchronous imports. FastAPI background execution is the local runtime; the
persistent queue boundary allows a later Celery, Dramatiq, or cloud worker to
claim the same task contract without changing the API.

Attempt-level audit records and aggregate metrics are documented in
`docs/knowledge-observability.md`.

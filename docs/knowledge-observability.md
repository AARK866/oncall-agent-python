# Knowledge ingestion observability

The ingestion subsystem exposes per-run performance data, immutable retry
attempts, aggregate metrics, structured logs, and an isolated real-stack
acceptance check.

## Per-run telemetry

Every `KnowledgeIngestResponse` includes `metadata.observability`:

- `elapsed_ms`;
- `documents_per_second`;
- `chunks_per_second`;
- `vectors_upserted`;
- `stale_vectors_deleted`.

Task execution logs use stable event names and identifiers:

```text
knowledge_ingestion_submitted task_id=...
knowledge_ingestion_started task_id=... attempt=...
knowledge_ingestion_succeeded task_id=... documents=... chunks=... elapsed_ms=...
knowledge_ingestion_failed task_id=... attempt=... error_type=...
```

API keys and document content are not included in these log messages.

## Audit and metrics APIs

Get every execution attempt for one task, including failures preserved before a
successful retry:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/ingestion-tasks/{task_id}/attempts
```

Get a 24-hour operational summary:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/knowledge/ingestion-metrics?window_hours=24"
```

The summary reports task counts by status, success rate, average and P95
duration, retry volume, processed documents, created chunks, upserted vectors,
and deleted stale vectors. `success_rate` uses completed tasks as its denominator;
queued and running tasks are excluded.

## Real-stack acceptance

Run the destructive-safe acceptance check only after real Embedding and Milvus
settings are active:

```powershell
.\.venv\Scripts\python.exe scripts\check_real_knowledge_pipeline.py
```

The check creates an isolated Milvus collection and validates:

1. a real embedding call and initial vector upsert;
2. unchanged-document detection with zero new chunks;
3. changed-document reindexing;
4. vector retrieval of the updated content;
5. automatic removal of the isolated collection.

The script never uses the production collection configured in `.env` and does
not print credentials.

# Knowledge Ingestion

This step adds a repeatable ingestion pipeline for runbooks.

The pipeline does four things:

```text
load markdown -> enrich metadata -> split chunks -> upsert vector store
```

## Sources

Local directory:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source local --path app/data/runbooks
```

GitHub repository path:

```powershell
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source github --path app/data/runbooks
```

GitHub mode uses:

```text
GITHUB_TOKEN
GITHUB_REPO
GITHUB_BRANCH
GITHUB_BASE_URL
```

## API

Trigger ingestion through the backend:

```http
POST /api/knowledge/ingest
```

Example body:

```json
{
  "source": "local",
  "path": "app/data/runbooks",
  "chunk_size": 800,
  "chunk_overlap": 120
}
```

The response includes:

```json
{
  "status": "ok",
  "source": "local",
  "documents_loaded": 2,
  "chunks_created": 4,
  "vector_store": "milvus",
  "collection_name": "oncall_runbook_chunks"
}
```

## Milvus

To persist chunks in Milvus:

```text
KNOWLEDGE_VECTOR_STORE=milvus
MILVUS_URI=http://localhost:19530
MILVUS_COLLECTION_NAME=oncall_runbook_chunks
```

The pipeline uses the configured embedding provider. For your local Ollama bge-m3 setup:

```text
EMBEDDING_PROVIDER=openai-compatible
EMBEDDING_MODEL=bge-m3
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_DIMENSIONS=1024
```

For local tests or learning mode, use:

```text
KNOWLEDGE_VECTOR_STORE=in_memory
EMBEDDING_PROVIDER=hash
```

## Notes

- Re-running ingestion upserts by `chunk_id`, so existing chunks are replaced.
- Metadata enrichment infers `services`, `incident_types`, and `tags` from title, path, and content.
- The pipeline is scriptable, so it can later be called from GitHub Actions, a scheduled worker, or an admin API.

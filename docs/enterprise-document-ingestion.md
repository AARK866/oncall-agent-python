# Enterprise document ingestion

The knowledge loader accepts `.md`, `.txt`, `.pdf`, and `.docx` files from a
local directory or GitHub repository. PDF and Word parsing use the official
LlamaIndex file readers.

## Metadata contract

Every document carries the same governance fields before chunking and vector
storage:

- `source_type` and `source_uri` identify origin;
- `source_version` identifies the local content hash or GitHub commit SHA;
- `updated_at` records local modification time when available;
- `content_sha256` supports deduplication and change detection;
- `access_scope` and `allowed_roles` provide an ACL foundation;
- `file_type`, `mime_type`, `file_size_bytes`, and `page_count` describe format;
- `parser` records the parser implementation.

Default settings:

```env
KNOWLEDGE_ALLOWED_EXTENSIONS=.md,.txt,.pdf,.docx
KNOWLEDGE_DEFAULT_ACCESS_SCOPE=internal
KNOWLEDGE_DEFAULT_ALLOWED_ROLES=oncall,sre
```

Run local ingestion:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source local --path app/data/runbooks
```

Run GitHub ingestion:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source github --path docs/runbooks
```

GitHub text and binary content follow the same Reader path. Temporary copies of
PDF and Word files are removed immediately after parsing.

ACL metadata is enforced during keyword, vector, and Milvus retrieval. See
`docs/knowledge-acl.md` for identity propagation and policy rules.

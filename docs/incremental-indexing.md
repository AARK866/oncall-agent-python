# Incremental knowledge indexing

Incremental indexing keeps the persistent Milvus collection synchronized with
local or GitHub documents without embedding every document on every run.

## Configuration

```env
KNOWLEDGE_INCREMENTAL_INDEXING_ENABLED=true
KNOWLEDGE_MANIFEST_DB_PATH=app/data/knowledge_manifest.db
KNOWLEDGE_VECTOR_STORE=milvus
```

The optimization is enabled only for Milvus. The in-memory vector store still
performs a full build because its data does not survive process restarts.

## Change detection

The SQLite manifest stores one record per source namespace and document:

- source URI and source version;
- a document signature covering content and ACL metadata;
- an index signature covering chunk, embedding, engine, and collection settings;
- the Milvus chunk IDs created for the document;
- the indexed timestamp.

A document is reindexed when its content, governance metadata, or indexing
configuration changes. Missing source documents are treated as deletions.

## Safe synchronization order

Each run follows this order:

1. load documents and compare them with the manifest;
2. split and embed only new or changed documents;
3. upsert their new chunks into Milvus;
4. delete old chunk IDs that were not replaced by the upsert;
5. commit manifest changes in one SQLite transaction.

The manifest advances only after vector operations succeed. A failed embedding
or Milvus request can therefore be retried without falsely marking a document as
current.

## Operations

Normal incremental run:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source local --path app/data/runbooks
```

Force all current documents through chunking and embedding:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source local --path app/data/runbooks --full-rebuild
```

The response metadata reports new, updated, unchanged, deleted, indexed, and
deleted-chunk counts for auditing and monitoring.

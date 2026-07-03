# Vector Store Setup

This project now supports three knowledge retrieval modes:

- `keyword`: token overlap search. This is the default and needs no extra setup.
- `vector`: local deterministic hash embedding plus in-memory vector search.
- `hybrid`: combines keyword and vector search results.

## 1. Default Mode

```env
KNOWLEDGE_RETRIEVER_MODE=keyword
```

This is stable for learning and tests.

## 2. Try Local Vector Search

Edit `.env`:

```env
KNOWLEDGE_RETRIEVER_MODE=vector
KNOWLEDGE_VECTOR_STORE=in_memory
```

Then run:

```powershell
python -m uvicorn app.main:app --reload
```

Send a request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/chat `
  -ContentType "application/json" `
  -Body '{"message":"database connection pool exhausted in payment","session_id":"vector-test","mode":"knowledge"}'
```

The response sources include metadata:

```text
metadata.retriever = vector
```

## 3. Hybrid Mode

```env
KNOWLEDGE_RETRIEVER_MODE=hybrid
```

Hybrid mode is useful when exact keywords and semantic similarity should both influence ranking.

## 4. Milvus Vector Store

The current vector store can be either:

- `in_memory`: simple local vector store.
- `milvus`: production vector database through `MilvusVectorStore`.

Use Milvus after a real embedding service is configured:

```env
EMBEDDING_PROVIDER=langchain-openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_DIMENSIONS=1536

KNOWLEDGE_RETRIEVER_MODE=hybrid
KNOWLEDGE_VECTOR_STORE=milvus
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION_NAME=oncall_runbook_chunks
```

The retrieval flow stays stable:

```text
query -> embedding -> vector store search -> SourceDocument[]
```

That keeps the Agent and API layers unchanged while the storage backend becomes production-grade.

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

## 4. Future Milvus Replacement

The current vector store is `InMemoryVectorStore`, which is intentionally simple.

Later, `MilvusVectorStore` can replace it behind the same search interface:

```text
query -> embedding -> vector store search -> SourceDocument[]
```

That keeps the Agent and API layers unchanged while the storage backend becomes production-grade.

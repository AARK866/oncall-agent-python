# Enterprise Environment Configuration

This project keeps local defaults runnable, then switches to real services through `.env`.

Do not commit `.env`. It contains secrets and is ignored by Git.

## 1. Real LLM

Use LangChain with an OpenAI-compatible chat API:

```env
LLM_PROVIDER=langchain-openai
LLM_MODEL=deepseek-chat
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=6
```

Used by:

- `app.llm.create_llm_client`
- `KnowledgeAgent`
- `OpsAgent`
- `ConversationAgent`

## 2. Real Embedding

Use LangChain with an OpenAI-compatible embedding API:

```env
EMBEDDING_PROVIDER=langchain-openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_DIMENSIONS=1536
```

Used by:

- `app.rag.create_embedding_model`
- `InMemoryVectorStore`
- `MilvusVectorStore`

DeepSeek chat keys are for chat completion. If your provider does not expose embedding models,
use a separate embedding provider such as OpenAI, DashScope, or another OpenAI-compatible embedding service.

## 3. Real Milvus

Use Milvus as the production vector database:

```env
KNOWLEDGE_RETRIEVER_MODE=hybrid
KNOWLEDGE_VECTOR_STORE=milvus
MILVUS_URI=http://127.0.0.1:19530
MILVUS_TOKEN=
MILVUS_DB_NAME=
MILVUS_COLLECTION_NAME=oncall_runbook_chunks
MILVUS_VECTOR_FIELD=vector
MILVUS_PRIMARY_FIELD=chunk_id
MILVUS_METRIC_TYPE=COSINE
```

Used by:

- `KnowledgeBase._get_vector_store`
- `MilvusVectorStore.from_chunks`
- `/api/knowledge/search`
- `/api/chat` when routed to knowledge mode

## Recommended Switch Order

1. Fill `LLM_API_KEY`, set `LLM_PROVIDER=langchain-openai`, run `python scripts/check_llm_client.py`.
2. Fill `EMBEDDING_API_KEY`, set `EMBEDDING_PROVIDER=langchain-openai`.
3. Start Milvus, fill `MILVUS_URI`.
4. Set `KNOWLEDGE_RETRIEVER_MODE=hybrid` and `KNOWLEDGE_VECTOR_STORE=milvus`.
5. Run the API and test `/api/knowledge/search`.

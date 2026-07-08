# Project acceptance

This document defines the current delivery boundary for the Python OnCall Agent project.
It is meant to answer one practical question: can the project be verified as a runnable
incident diagnosis backend instead of a set of isolated demos?

## Implemented capabilities

| Area | Current capability |
| --- | --- |
| API service | FastAPI application with health, chat, incidents, knowledge, tools, alerts, and async task APIs. |
| Agent runtime | Local graph runtime plus ReAct and plan-execute style diagnosis flows. |
| LLM | Mock LLM for local development and LangChain OpenAI-compatible client for DeepSeek. |
| Knowledge | Markdown runbook loading, metadata enrichment, chunking, keyword/vector/hybrid retrieval, and ingestion. |
| Embeddings | Hash embeddings for deterministic tests and OpenAI-compatible embeddings for Ollama bge-m3. |
| Vector store | In-memory vector store for tests and Milvus connector for realistic deployment. |
| Ops tools | Mock tools plus real Prometheus, Loki, and GitHub clients. |
| Alerts | Alertmanager webhook, signed webhook verification, deduplication, alert groups, and task creation. |
| Async workflow | SQLite-backed task records, task events, background diagnosis execution, and incident persistence. |
| Security | API token protection for sensitive APIs and HMAC signature support for webhooks. |
| Delivery | Dockerfile, docker-compose, GitHub Actions CI, and smoke/acceptance scripts. |

## Acceptance command

Run the default local-safe acceptance check:

```powershell
.\.venv\Scripts\python.exe scripts\run_acceptance.py
```

Default mode intentionally overrides risky external integrations:

- `LLM_PROVIDER=mock`
- `EMBEDDING_PROVIDER=hash`
- `KNOWLEDGE_VECTOR_STORE=in_memory`
- `OPS_TOOL_MODE=mock`
- API token auth disabled for the in-process test client
- webhook signing enabled with a temporary test secret

The command verifies:

1. health endpoint;
2. ops tool health;
3. knowledge ingestion;
4. knowledge search;
5. signed Alertmanager webhook;
6. async diagnosis task completion;
7. task progress events;
8. alert group creation;
9. incident history persistence.

Use JSON output when another script needs to consume the result:

```powershell
.\.venv\Scripts\python.exe scripts\run_acceptance.py --json
```

## Real integration checks

After `.env` is configured and the external services are running, use the focused checks first:

```powershell
.\.venv\Scripts\python.exe scripts\check_llm_client.py
.\.venv\Scripts\python.exe scripts\check_enterprise_stack.py
.\.venv\Scripts\python.exe scripts\ingest_knowledge.py --source local --path app/data/runbooks
```

Then run the acceptance script without local overrides:

```powershell
.\.venv\Scripts\python.exe scripts\run_acceptance.py --real-env
```

If you only want to keep the safe mock LLM and in-memory knowledge stack but call real
Prometheus, Loki, and GitHub tools, run:

```powershell
.\.venv\Scripts\python.exe scripts\run_acceptance.py --real-tools
```

## Production gaps

The project is now a runnable backend, but a production enterprise rollout should still add:

- a durable worker system such as Celery, Dramatiq, RQ, or a queue-backed LangGraph worker;
- Kubernetes manifests or a managed container deployment target;
- centralized traces, logs, and metrics for the agent itself;
- role-based auth or an identity provider instead of a single API token;
- a dashboard for task history, alert groups, incident reports, and runbook management;
- stronger evaluation datasets for diagnosis quality and retrieval quality.

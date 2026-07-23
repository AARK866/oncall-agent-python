# OnCall Agent Python

A learning-oriented OnCall Agent backend built with Python and FastAPI.

Useful commands:

```powershell
python -m uvicorn app.main:app --reload
python scripts/check_llm_client.py
python scripts/check_enterprise_stack.py
python scripts/check_real_incident_flow.py --mock-llm
python scripts/check_real_api_flow.py --in-process --mock-llm
python scripts/check_alert_webhook.py --in-process --mock-llm
python scripts/ingest_knowledge.py --source local --path app/data/runbooks
python scripts/check_real_knowledge_pipeline.py
python scripts/evaluate_rag.py --local-safe
python scripts/run_acceptance.py
docker compose up --build api
.\.venv\Scripts\python.exe -m pytest
```

Docs:

- Architecture: `docs/architecture.md`
- Integration roadmap: `docs/integration-roadmap.md`
- Enterprise env: `docs/enterprise-env.md`
- Deployment and CI/CD: `docs/deployment-ci.md`
- LLM setup: `docs/llm-setup.md`
- LangChain real LLM: `docs/langchain-real-llm.md`
- LangGraph setup: `docs/langgraph-setup.md`
- LangGraph run identity: `docs/langgraph-run-identity.md`
- LangGraph native checkpointing: `docs/langgraph-native-checkpointing.md`
- LangGraph native interrupt resume: `docs/langgraph-native-interrupt.md`
- LangGraph persistent checkpointer: `docs/langgraph-persistent-checkpointer.md`
- LangGraph node resume: `docs/langgraph-node-resume.md`
- Vector store setup: `docs/vector-store.md`
- Knowledge API: `docs/knowledge-api.md`
- Knowledge ingestion: `docs/knowledge-ingestion.md`
- LlamaIndex knowledge adapter: `docs/llamaindex-knowledge.md`
- RAG retrieval evaluation: `docs/rag-evaluation.md`
- Enterprise document ingestion: `docs/enterprise-document-ingestion.md`
- Knowledge ACL: `docs/knowledge-acl.md`
- Incremental knowledge indexing: `docs/incremental-indexing.md`
- Knowledge ingestion tasks: `docs/knowledge-ingestion-tasks.md`
- Knowledge ingestion observability: `docs/knowledge-observability.md`
- Workflow control-plane domain model: `docs/workflow-domain-model.md`
- Workflow validation and LangGraph runtime: `docs/workflow-validation-runtime.md`
- Workflow publishing, immutable versions, and rollback: `docs/workflow-versioning.md`
- Workflow observability, persisted review, and audit: `docs/workflow-observability-review.md`
- Real ops connectors: `docs/real-ops-connectors.md`
- Security and production: `docs/security-production.md`
- Tools health API: `docs/tools-health-api.md`
- Alert webhook: `docs/alert-webhook.md`
- Async tasks: `docs/async-tasks.md`
- Alert deduplication: `docs/alert-dedup.md`
- Project acceptance: `docs/project-acceptance.md`
- LangGraph reliability: `docs/langgraph-reliability.md`
- Error recovery: `docs/error-recovery.md`
- Human review: `docs/human-review.md`
- Retry policy: `docs/retry-policy.md`
- Task rerun: `docs/task-rerun.md`
- Task cancellation: `docs/task-cancellation.md`
- Task timeout recovery: `docs/task-timeout-recovery.md`

# RAG retrieval evaluation

The retrieval evaluator turns RAG quality into a repeatable test instead of a
manual impression.

## Dataset

Evaluation cases live in `app/data/evaluation/rag_cases.jsonl`. Each JSONL row
contains a query and one or more expected source document ids:

```json
{"case_id":"payment-5xx","query":"payment-api 5xx","expected_doc_ids":["payment_5xx.md"],"top_k":3}
```

Optional `service`, `incident_type`, and `keywords` fields exercise the same
metadata filters used by the production knowledge API.

## Metrics

- Hit Rate: whether at least one expected document appears in the top-k results.
- MRR: the reciprocal rank of the first expected document.

The implementation uses LlamaIndex's `HitRate` and `MRR` metric classes. It
evaluates document ids instead of chunk ids, so changing chunk size does not
create a false quality regression.

## Local-safe run

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rag.py --local-safe
```

This uses hash embeddings and the in-memory vector store. It does not call
Ollama, Milvus, or an external model.

## Real-stack run

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rag.py
```

This uses the current `.env`, including the configured embedding model, Milvus,
retriever mode, and reranker.

Use JSON output in CI:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rag.py --local-safe --json
```

The command exits with code 1 when either threshold fails. Defaults are Hit
Rate >= 0.8 and MRR >= 0.7; override them with `--min-hit-rate` and `--min-mrr`.

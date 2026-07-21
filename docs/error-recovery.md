# Error recovery

This step adds bounded fallback paths around the most fragile external
dependencies in the OnCall Agent flow.

## Retrieval fallback

When knowledge retrieval is configured as `vector` or `hybrid`, the system may
depend on an embedding service and Milvus. If either dependency fails, the
knowledge base now falls back to keyword search instead of raising a 500 error.

The returned sources include recovery metadata:

```json
{
  "retriever": "keyword",
  "recovery": {
    "used": true,
    "fallback_from": "hybrid",
    "fallback_to": "keyword",
    "error_type": "RuntimeError"
  }
}
```

This directly protects the path that can fail when Ollama, Milvus, CUDA, or a
remote embedding API is unavailable.

## LLM fallback

If the knowledge agent retrieves runbook sources but the LLM call fails, it now
returns a deterministic answer built from the retrieved runbook snippets.

That keeps the API usable during model outages:

```text
retrieved runbook sources -> LLM fails -> snippet-based fallback answer
```

The response metadata includes:

```json
{
  "llm_fallback": {
    "used": true,
    "reason": "llm_error"
  }
}
```

## Why it matters

This is the second LangGraph reliability step. The first step made graph
execution visible through checkpoints. This step makes common dependency
failures recoverable, so one broken subsystem does not take down the full
diagnosis flow.

## Current boundary

The fallback is intentionally limited and explainable:

- vector or hybrid retrieval falls back only to keyword retrieval;
- LLM answer generation falls back only after documents have been retrieved;
- the original error type and message are kept in metadata;
- high-risk operations are not retried automatically.

# Retry policy

This step adds a bounded retry policy for tool execution.

## Scope

The retry policy is applied at `ToolRegistry.execute`, so every registered ops
tool gets the same behavior:

```text
Agent -> ToolRegistry -> retry policy -> tool handler
```

That protects real external connectors such as Prometheus, Loki, GitHub, and
GitLab without duplicating retry loops in every client.

## What is retried

The policy retries failures that look transient:

- timeout errors;
- connection errors;
- network/temporary/remote protocol style errors;
- HTTP status codes `408`, `409`, `425`, `429`, `500`, `502`, `503`, and `504`.

It does not retry deterministic application errors such as invalid arguments or
missing configuration.

## Configuration

```env
TOOL_RETRY_MAX_ATTEMPTS=3
TOOL_RETRY_BASE_DELAY_SECONDS=0.05
TOOL_RETRY_MAX_DELAY_SECONDS=1.0
```

The delay uses a small exponential backoff:

```text
0.05s -> 0.10s -> 0.20s, capped by TOOL_RETRY_MAX_DELAY_SECONDS
```

## Response metadata

When a tool needs more than one attempt, the result data includes `_retry`:

```json
{
  "_retry": {
    "attempts": 3,
    "retried": true,
    "errors": ["TimeoutError: temporary timeout"]
  }
}
```

Failed tool results also keep retry metadata in `data._retry`, so task events can
show whether the system failed immediately or exhausted a retry budget.

## Why it matters

This moves the project closer to LangGraph/Harness-style reliable execution:

```text
not every failure is equal -> classify -> retry only bounded transient failures
```

The retry loop is intentionally small and explainable. It avoids hidden infinite
retries and keeps enough metadata for incident debugging.

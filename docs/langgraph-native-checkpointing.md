# LangGraph native checkpointing

This step connects the ops graph to LangGraph's native checkpointer interface.

## What Changed

When `OPS_GRAPH_RUNTIME=langgraph`, the workflow now compiles the graph with a
checkpointer:

```text
StateGraph -> compile(checkpointer=MemorySaver) -> ainvoke(config.thread_id)
```

The project still writes its own readable SQLite checkpoints for task APIs, but
LangGraph now also receives the native checkpointer required for thread-level
resume.

## Configuration

```env
OPS_GRAPH_RUNTIME=langgraph
OPS_GRAPH_CHECKPOINTER=memory
```

Supported values:

- `memory`: use LangGraph's in-process `MemorySaver`;
- `auto`: use `MemorySaver` when available;
- `disabled`: compile LangGraph without a checkpointer.

## Response Metadata

Check one task result:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}
```

Look at:

```text
result.metadata.graph_runtime.used
result.metadata.graph_runtime.checkpointer_used
```

Expected values:

```text
used = langgraph
checkpointer_used = memory
```

## Current Limit

`MemorySaver` is process-local. It proves the native LangGraph checkpointer
contract and supports resume while the same API process is alive, but it is not
a durable production backend.

For production, replace it with a durable LangGraph checkpointer such as SQLite
or Postgres when the matching package is installed.

## Why It Matters

The graph now has the two ingredients needed for real resume:

```text
thread_id + native checkpointer
```

The next deep step can use this foundation to continue from an interrupted
LangGraph thread instead of rerunning the whole diagnosis from the beginning.

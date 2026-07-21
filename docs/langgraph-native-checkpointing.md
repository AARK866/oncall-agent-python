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
- `sqlite`: use LangGraph's SQLite checkpointer when
  `langgraph-checkpoint-sqlite` is installed;
- `auto`: use `MemorySaver` when available;
- `disabled`: compile LangGraph without a checkpointer.

SQLite configuration:

```env
OPS_GRAPH_RUNTIME=langgraph
OPS_GRAPH_CHECKPOINTER=sqlite
OPS_GRAPH_CHECKPOINT_DB_PATH=app/data/langgraph_checkpoints.sqlite
```

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

For production-like local deployment, use `sqlite`. For a clustered production
deployment, replace SQLite with a durable shared backend such as Postgres when
the matching LangGraph checkpointer package is installed.

The state sent into LangGraph is JSON-friendly:

```text
dict/list/str/number/bool/null
```

The application restores that snapshot into `OpsGraphState` only inside node
execution. This avoids persisting app-specific Python objects such as Pydantic
models or enums in the LangGraph checkpoint stream.

## Why It Matters

The graph now has the two ingredients needed for real resume:

```text
thread_id + native checkpointer
```

Node-level resume now uses this foundation together with the readable SQLite
checkpoint snapshots to continue after the latest completed node instead of
rerunning the whole diagnosis from the beginning.

# LangGraph run identity

This step adds graph run identity to every ops diagnosis.

## Concepts

- `thread_id`: stable execution thread for one incident or conversation line;
- `run_id`: one concrete execution attempt inside that thread.

For alert-driven tasks, `thread_id` is derived from the alert group:

```text
thread_{alert_group_id}
```

For direct chat ops diagnosis, `thread_id` is derived from `session_id`:

```text
thread_session_{session_id}
```

Each new task gets a fresh `run_id`.

## Rerun Behavior

Task reruns keep the same `thread_id` and create a new `run_id`:

```text
original task: thread_A, run_1
rerun task:    thread_A, run_2
```

That gives the system a clean lineage:

```text
same incident thread -> multiple execution attempts
```

## Persistence

The identity is stored in:

- `diagnosis_tasks.thread_id`
- `diagnosis_tasks.run_id`
- `ops_graph_checkpoints.thread_id`
- `ops_graph_checkpoints.run_id`
- `ChatResponse.metadata.graph_run`
- `ChatResponse.metadata.trigger`

## LangGraph Runtime

When `OPS_GRAPH_RUNTIME=langgraph`, the workflow passes the identity into the
LangGraph runnable config:

```text
config.configurable.thread_id = thread_id
config.metadata.thread_id = thread_id
config.metadata.run_id = run_id
```

The implementation still keeps our SQLite checkpoint records because they are
easy to inspect through the task API. When `OPS_GRAPH_RUNTIME=langgraph`, the
same identity is also passed into LangGraph's native checkpointer config.

## Why It Matters

Without these two IDs, rerun, resume, approval continuation, and audit logs all
have to infer relationships from timestamps or task ids. With them, the graph
has an explicit execution identity:

```text
thread_id = what workflow line is this?
run_id    = which attempt is this?
```

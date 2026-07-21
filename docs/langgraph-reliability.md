# LangGraph reliability upgrade

This step borrows the most important production idea from LangGraph-style
agents: an agent graph should not be an invisible in-memory chain. Each node
should have explicit state that can be inspected after the run.

## What changed

The ops diagnosis graph now writes checkpoints while it runs:

```text
infer_service started
infer_service completed
plan started
plan completed
...
persist_incident completed
```

Each checkpoint stores:

- task id;
- graph node name;
- status: `started`, `completed`, or `failed`;
- a compact state snapshot;
- error text when a node fails;
- creation time.

## New API

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/checkpoints
```

This is protected by the same task API auth policy as task details and task
events.

## Why it matters

Before this step, a failed background diagnosis could tell you only that the
whole task failed. Now it can show the exact graph node where execution stopped.

That gives the project a foundation for:

- retrying from a known failed node;
- human review before risky nodes;
- durable worker execution;
- replaying and debugging incident diagnosis runs;
- exposing a Dify-like workflow trace in a future UI.

## Current boundary

This step does not yet resume execution from a checkpoint. It only persists the
graph execution trail. The next LangGraph step can use these checkpoints to add
bounded retry and manual review gates.

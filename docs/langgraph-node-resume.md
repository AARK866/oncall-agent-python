# LangGraph node resume

This step adds node-level resume for ops diagnosis tasks.

## What It Does

`rerun` starts a diagnosis from the beginning.

`resume` creates a new task from the latest completed graph checkpoint and runs
only the remaining nodes.

```text
original task
  infer_service completed
  plan completed
  select_tools completed
  execute_tools completed
  worker crashed

resume task
  starts at retrieve_runbook
  continues through persist_incident
```

The original task stays failed or timed out. The resumed task gets its own
`task_id` and `run_id`, while keeping the same `thread_id`.

## API

Resume a failed, timed-out, or canceled task:

```powershell
$body = @{
  requested_by = "alice"
  reason = "Continue after worker crash."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/tasks/{task_id}/resume `
  -ContentType "application/json" `
  -Body $body
```

List resume attempts:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/resumes
```

Read original task events:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/events
```

You should see a `resume_requested` event with:

- new task id;
- checkpoint id;
- node name that resume continues after.

## Stored Fields

The resumed task stores:

- `resume_of_task_id`: original task id;
- same `thread_id` as the original task;
- new `run_id`;
- `trigger_metadata.resume.checkpoint_id`;
- `trigger_metadata.resume.after_node`;
- `trigger_metadata.resume.requested_by`;
- `trigger_metadata.resume.reason`.

## How Resume Chooses The Start Node

The queue looks at the original task checkpoints:

```text
latest checkpoint where status = completed
```

Then the graph starts from the next node in the fixed ops graph order.

If no completed checkpoint exists, the resume task starts from the first node.

## Harness Connection

This is the practical resume path:

```text
checkpoint snapshot -> restore graph state -> skip completed nodes -> run remaining nodes
```

The implementation still keeps task-level audit records in SQLite. When
`OPS_GRAPH_RUNTIME=langgraph`, the remaining-node graph can also run with the
native LangGraph checkpointer enabled.

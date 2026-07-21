# Task rerun

This step adds a manual rerun entrypoint for diagnosis tasks.

## Why

Production diagnosis may fail because a dependent system is temporarily down, a
connector token is expired, or a runbook/vector index has just been updated.
Instead of overwriting the old task, the system creates a new task and links it
back to the original one.

```text
original task -> rerun_requested event -> new diagnosis task
```

That keeps the audit trail intact while still giving operators a fast recovery
path.

## API

Rerun a completed task:

```powershell
$body = @{
  requested_by = "alice"
  reason = "Run again after fixing the Loki connector."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/tasks/{task_id}/rerun `
  -ContentType "application/json" `
  -Body $body
```

List reruns created from a task:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/reruns
```

Read the original task events:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/events
```

You should see a `rerun_requested` event with the new task id.

## Data Model

The new task stores:

- `rerun_of_task_id`: direct parent task id;
- `trigger_metadata.rerun.of_task_id`: direct parent task id;
- `trigger_metadata.rerun.root_task_id`: first task in the rerun chain;
- `trigger_metadata.rerun.requested_by`: operator or system that requested it;
- `trigger_metadata.rerun.reason`: operator-supplied reason.

## Safety

By default, only terminal tasks can be rerun:

- `succeeded`
- `failed`

If a task is still `queued` or `running`, the API returns `409 Conflict`. An
operator can pass `force=true` when they intentionally want to duplicate an
active task.

## Harness Connection

This is the first step toward LangGraph-style resume behavior:

```text
checkpointed graph state + task lineage + explicit recovery event
```

The current implementation reruns from the original task input. A later worker
upgrade can use saved graph checkpoints to resume from a failed node instead of
starting from the beginning.

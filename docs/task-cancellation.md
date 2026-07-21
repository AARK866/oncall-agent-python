# Task cancellation

This step adds operator-controlled cancellation for diagnosis tasks.

## Why

In production, an incident diagnosis may become unnecessary because:

- another operator has taken manual ownership;
- the alert was confirmed to be noise;
- a duplicate task was created;
- a high-cost investigation should be stopped before calling more tools.

The system now exposes an explicit cancellation API instead of relying on
deleting rows or killing a worker process.

## API

Cancel a task:

```powershell
$body = @{
  requested_by = "alice"
  reason = "Duplicate investigation."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/tasks/{task_id}/cancel `
  -ContentType "application/json" `
  -Body $body
```

Read the task events:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/events
```

You should see:

```text
queued -> cancel_requested -> canceled
```

## Status Behavior

- `queued` tasks are canceled immediately.
- `running` tasks become `cancel_requested`.
- the graph runner checks cancellation before each node and stops at the next
  safe boundary.
- `succeeded`, `failed`, and `canceled` tasks are terminal.

## Harness Connection

This adds a control-plane command to the Agent Harness:

```text
operator -> task API -> task store -> graph node boundary check -> canceled
```

The important design point is that cancellation is not hidden inside one worker
process. It is represented in persistent task state and event history, so future
distributed workers can honor the same contract.

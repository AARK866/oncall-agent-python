# Task timeout recovery

This step adds recovery for stale active diagnosis tasks.

## Why

In production, a worker can disappear while a task is still marked `running`.
Common causes include:

- worker process crash;
- host restart;
- long network stall while calling an LLM or ops connector;
- deployment during task execution.

Without recovery, the task stays `running` forever and operators cannot tell
whether it is still doing useful work.

## API

Recover stale active tasks:

```powershell
$body = @{
  requested_by = "watchdog"
  reason = "Worker heartbeat expired."
  max_age_seconds = 900
  limit = 50
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/tasks/recover-stale `
  -ContentType "application/json" `
  -Body $body
```

The response returns the tasks that were recovered.

## Status Behavior

- stale `running` tasks become `timed_out`;
- stale `cancel_requested` tasks become `canceled`;
- recent active tasks are ignored;
- `timed_out` tasks can be rerun through `POST /api/tasks/{task_id}/rerun`.

## Configuration

```env
DIAGNOSIS_TASK_TIMEOUT_SECONDS=900
DIAGNOSIS_TASK_RECOVERY_LIMIT=50
```

The API can override both values per request with `max_age_seconds` and `limit`.

## Harness Connection

This is the task-system equivalent of a watchdog:

```text
task store -> find stale active tasks -> classify -> mark terminal -> append event
```

It keeps failure recovery bounded and explainable. A later cron scheduler or
worker supervisor can call the same recovery method on a fixed interval.

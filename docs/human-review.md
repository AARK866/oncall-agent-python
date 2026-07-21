# Human review gate

This step adds a human review gate for high-risk proposed actions.

The agent can still investigate incidents automatically. Read-only operations
such as metrics, logs, deployments, commits, topology, and runbook retrieval keep
running without approval. The gate only activates when the diagnosis report
proposes risky production actions such as:

- rollback;
- restart;
- scale out or scale in;
- traffic switching;
- degradation/failover actions.

## Flow

```text
OpsGraph diagnosis
  -> build report
  -> build response
  -> human_review_gate
       -> no risky action: continue
       -> risky action: create pending review request and pause task
  -> persist incident and diagnosis
```

When the gate pauses, the task status becomes:

```text
waiting_review
```

The diagnosis draft is stored on the task result, but incident persistence waits
until approval. Approving the review resumes the task from the paused graph
checkpoint. Rejecting the review marks the task failed and keeps the rejection
reason on the task.

## APIs

List pending reviews:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/reviews?status=pending"
```

List reviews for one task:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/reviews
```

Approve:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/reviews/{review_id}/approve `
  -ContentType "application/json" `
  -Body '{"reviewer":"alice","reason":"Deployment owner confirmed rollback plan."}'
```

Reject:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/reviews/{review_id}/reject `
  -ContentType "application/json" `
  -Body '{"reviewer":"bob","reason":"Need database owner confirmation first."}'
```

## Why it matters

This moves the project from a trust-based tool execution model toward a
Harness-style safety pipeline:

```text
agent proposes -> harness gates -> human decides -> execution can proceed later
```

The current implementation creates and tracks the approval decision. It does not
yet execute approved write operations. That is intentional: execution tools
should be added only after this gate exists.

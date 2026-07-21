# LangGraph native interrupt resume

This step upgrades the human review gate to use LangGraph's native
`interrupt()` and `Command(resume=...)` path when LangGraph runtime and a native
checkpointer are enabled.

## Configuration

```env
OPS_GRAPH_RUNTIME=langgraph
OPS_GRAPH_CHECKPOINTER=memory
```

`MemorySaver` is still process-local. It proves the LangGraph contract while
the API process is alive. A later production step should replace it with a
durable checkpointer.

## Runtime Flow

```text
alert webhook
  -> diagnosis task queued
  -> LangGraph StateGraph starts
  -> human_review_gate
       -> create or reuse review request
       -> interrupt(payload)
  -> task status waiting_review

operator approves review
  -> /api/reviews/{review_id}/approve
  -> DiagnosisTaskQueue.run(task_id)
  -> Command(resume={ approved: true, review_ids: [...] })
  -> LangGraph resumes at human_review_gate
  -> persist_incident
  -> task status succeeded
```

The human review gate is idempotent because LangGraph re-executes the interrupted
node from the beginning on resume. The gate looks for an existing review for the
same task and run before creating a new one.

## How To Verify

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Trigger a high-risk alert, then read the task:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}
```

Before approval:

```text
status = waiting_review
result.metadata.human_review.status = pending
```

Approve the review:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/reviews/{review_id}/approve `
  -ContentType "application/json" `
  -Body '{"reviewer":"alice","reason":"Approved rollback gate."}'
```

After approval:

```text
status = succeeded
result.metadata.graph_runtime.reason = native_interrupt_resume
result.metadata.human_review.status = approved
```

Check graph checkpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{task_id}/checkpoints
```

You should see `human_review_gate` with both:

```text
paused
completed
```

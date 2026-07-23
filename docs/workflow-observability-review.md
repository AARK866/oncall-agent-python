# Workflow Observability and Human Review

Workflow execution is now a persisted state machine instead of a synchronous
request with no durable history. Every run keeps the graph snapshot that created
it, uses an isolated LangGraph checkpoint namespace, and exposes node-level
events, review state, metrics, and audit records.

## Runtime Storage

```text
workflow_applications
    |
    +-- workflow_runs
    |       |
    |       +-- workflow_run_events
    |       +-- workflow_review_requests
    |
    +-- workflow_audit_events
```

- `workflow_runs` stores source type, draft/version identity, inputs, output,
  status, duration timestamps, actor, graph snapshot, and graph hash.
- `workflow_run_events` is an append-only node and lifecycle timeline.
- `workflow_review_requests` stores interrupt payloads and review decisions.
- `workflow_audit_events` records publish, rollback, run, and review actions.

## Checkpoint Configuration

Production defaults to the SQLite LangGraph checkpointer:

```env
WORKFLOW_CHECKPOINTER=sqlite
WORKFLOW_CHECKPOINT_DB_PATH=app/data/workflow_langgraph_checkpoints.sqlite
```

Install the declared project dependencies before starting the API:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Each run uses `(thread_id, run_id)` as its checkpoint identity. Reusing a
user-facing `thread_id` therefore cannot merge state from unrelated runs.

## Run Lifecycle

```text
running
   |
   +-- succeeded
   +-- failed
   +-- waiting_review -- approved --> running --> succeeded/failed
                         rejected --> rejected
```

The runtime emits `node_started`, `node_completed`, `node_paused`, and
`node_failed` events. Completed events include node latency. Errors are truncated
and configured secrets are redacted before persistence.

When a `human_review` node calls LangGraph `interrupt()`:

1. LangGraph saves the checkpoint.
2. The run becomes `waiting_review`.
3. The interrupt ID and payload become a pending review record.
4. Approval sends `Command(resume=...)` to the same checkpoint.
5. Previously completed nodes are not executed again.
6. Rejection moves the run to the terminal `rejected` state.

Published and draft runs both save their graph snapshot. A later draft edit or
version publication cannot change a waiting run before it resumes.

## API

```text
GET  /api/workflow-apps/{app_id}/runs
GET  /api/workflow-apps/{app_id}/runs/metrics
GET  /api/workflow-apps/{app_id}/runs/{run_id}
GET  /api/workflow-apps/{app_id}/runs/{run_id}/events
GET  /api/workflow-apps/{app_id}/runs/{run_id}/reviews
POST /api/workflow-apps/{app_id}/runs/{run_id}/reviews/{review_id}/approve
POST /api/workflow-apps/{app_id}/runs/{run_id}/reviews/{review_id}/reject
GET  /api/workflow-apps/{app_id}/audit-events
```

Run listing supports `status` and `limit`. Metrics support `window_hours` and
return counts by status, success rate, average/P95 duration, and pending review
count.

Review decisions use optimistic state transitions. Deciding an already decided
review, or deciding a review after its run reached a terminal state, returns
HTTP `409`.

## Audit Identity

In local development, caller-provided names remain available for easy testing.
When API token authentication is active, the server replaces caller-provided
actor names with `API_TOKEN_SUBJECT`. This prevents a client from forging the
publisher, runner, rollback requester, or reviewer stored in the audit log.

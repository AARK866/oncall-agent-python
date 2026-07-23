# Workflow Management Console

The workflow management console is the final product-facing layer of the
workflow control plane. It provides a browser UI for designing, publishing,
running, reviewing, and observing OnCall Agent workflows.

## Start the console

From the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/console
```

The console is served directly by FastAPI, so it does not need a Node.js build
step. By default it calls the API from the same origin.

## Connection settings

Use the connection button in the top bar when the API is hosted at another
address or when API token authentication is enabled.

- `API URL`: leave empty to use the current FastAPI origin.
- `API token`: use the token configured by the backend.

Connection settings are kept in browser `sessionStorage`. For production
deployments, expose the console only through HTTPS and keep API authorization
enabled.

## Main views

### Designer

The designer edits the current workflow draft.

- Create an application or select an existing application.
- Add agent, tool, condition, and human-review nodes.
- Add directed edges between nodes.
- Drag nodes to arrange the canvas.
- Select a node to edit its label and JSON configuration.
- Edit the global workflow configuration in the inspector.
- Validate before saving or publishing.

Saving uses the draft revision returned by the server. If another client has
already updated that revision, the backend rejects the stale write instead of
silently overwriting it.

### Versions

Publishing creates an immutable workflow version. The versions view can:

- inspect published version metadata;
- run a specific historical version;
- roll the active draft back to a previous version.

Rollback creates a new draft revision. It does not mutate the old published
version.

### Runs

The runs view shows execution status and aggregate metrics. Opening a run
reveals:

- node execution events;
- structured output;
- pending human-review requests;
- review decisions and resume results.

Approving or rejecting a pending review resumes the persisted LangGraph run
from its checkpoint.

### Audit

The audit view records workflow lifecycle actions such as draft updates,
publication, execution, rollback, and review decisions. These records support
incident investigation and operational accountability.

## Recommended operating flow

1. Create an application.
2. Build a draft in the designer.
3. Validate and save the draft.
4. Publish an immutable version.
5. Run that version with a JSON payload.
6. Handle any pending review request.
7. Inspect the run timeline, metrics, and audit trail.

## Acceptance check

Run the isolated end-to-end console acceptance script:

```powershell
.\.venv\Scripts\python.exe scripts\check_workflow_console.py
```

The script verifies static assets, application creation, draft updates,
validation, publication, execution interruption, human review resume, node
events, metrics, and audit records without changing the normal workflow
database.

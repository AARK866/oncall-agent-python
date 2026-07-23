# Workflow control-plane domain model

The Dify-inspired control plane stores editable workflow applications separately
from the LangGraph runtime. This keeps product configuration independent from
execution state, checkpoints, and knowledge indexing.

## Entities

`WorkflowApplicationRecord` owns the product identity:

- `app_id`, name, and description;
- `active` or `archived` lifecycle status;
- creation and update timestamps.

Each application receives one `WorkflowDraftRecord` in the same database
transaction. A draft contains:

- schema version;
- nodes and node-specific configuration;
- directed edges, conditions, and priorities;
- workflow variables and global settings;
- an integer `revision` used for optimistic concurrency.

Supported initial node types are `start`, `agent`, `knowledge_retrieval`, `tool`,
`human_review`, and `end`. An empty graph is valid while a workflow is first
being designed, but it must pass validation before execution. See
`docs/workflow-validation-runtime.md` for compilation and runtime rules.

## Persistence

```env
WORKFLOW_DB_PATH=app/data/workflows.db
```

SQLite tables:

```text
workflow_applications (1) ---- (1) workflow_drafts
           |
           +--------------- (N) workflow_versions
```

The foreign key prevents orphan drafts. Applications are archived rather than
physically deleted, preserving future version and execution references.

## API

```text
POST  /api/workflow-apps
GET   /api/workflow-apps
GET   /api/workflow-apps/{app_id}
PATCH /api/workflow-apps/{app_id}
GET   /api/workflow-apps/{app_id}/draft
PUT   /api/workflow-apps/{app_id}/draft
```

Published versions, rollback semantics, and version execution are documented in
`docs/workflow-versioning.md`.

Draft updates must provide `expected_revision`. If another editor has already
saved a newer draft, the API returns HTTP `409` with both expected and current
revisions instead of silently overwriting work.

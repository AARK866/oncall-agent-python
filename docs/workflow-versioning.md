# Workflow Versioning

The workflow control plane separates editable drafts from immutable production
versions. A deployment always points to a numbered version, never to a mutable
draft.

## Guarantees

- A draft must pass the same structural and node configuration validation used
  by the LangGraph compiler before it can be published.
- Version numbers increase monotonically per workflow application.
- A published graph, content hash, publisher, release notes, and source draft
  revision are immutable.
- Publishing the same draft revision more than once is idempotent and returns
  the existing version.
- Rollback copies an old version into a new draft revision. It never updates or
  deletes version history.
- A published version can be executed directly, so later draft edits cannot
  change production behavior.
- Publish and rollback actions are written to the workflow audit log.

## Storage Model

```text
workflow_applications (1) ---- (1) workflow_drafts
           |
           +--------------- (N) workflow_versions
```

`workflow_versions` has unique constraints on `(app_id, version_number)` and
`(app_id, source_draft_revision)`. The canonical graph JSON is protected by a
SHA-256 content hash.

## API

```text
POST /api/workflow-apps/{app_id}/publish
GET  /api/workflow-apps/{app_id}/versions
GET  /api/workflow-apps/{app_id}/versions/{version_number}
POST /api/workflow-apps/{app_id}/versions/{version_number}/run
POST /api/workflow-apps/{app_id}/versions/{version_number}/rollback
```

Publish and rollback requests carry `expected_revision`. A stale editor receives
HTTP `409` instead of silently overwriting another user's draft.

## Lifecycle

```text
edit draft -> validate -> publish immutable v1 -> edit draft -> publish v2
                                      |
                                      +-> run v1
                                      |
                                      +-> copy v1 into a new draft revision
```

Publishing a rollback-restored draft creates the next version number. For
example, restoring `v1` after `v2` and publishing it produces `v3`; history
remains `v1`, `v2`, `v3`.

Runtime events, persisted approvals, and audit query APIs are documented in
`docs/workflow-observability-review.md`.

from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationRecord,
    WorkflowApplicationUpdate,
    WorkflowDraftRecord,
    WorkflowDraftUpdate,
)
from app.storage import SQLiteWorkflowStore


class WorkflowService:
    """Application service for the workflow control plane."""

    def __init__(self, store: SQLiteWorkflowStore | None = None) -> None:
        self.store = store or SQLiteWorkflowStore.from_settings()

    def create(self, request: WorkflowApplicationCreate) -> WorkflowApplicationRecord:
        application, _ = self.store.create_application(request)
        return application

    def list(
        self,
        limit: int = 20,
        include_archived: bool = False,
    ) -> list[WorkflowApplicationRecord]:
        return self.store.list_applications(
            limit=limit,
            include_archived=include_archived,
        )

    def get(self, app_id: str) -> WorkflowApplicationRecord | None:
        return self.store.get_application(app_id)

    def update(
        self,
        app_id: str,
        request: WorkflowApplicationUpdate,
    ) -> WorkflowApplicationRecord:
        return self.store.update_application(app_id, request)

    def get_draft(self, app_id: str) -> WorkflowDraftRecord:
        self.store.require_application(app_id)
        return self.store.require_draft(app_id)

    def update_draft(
        self,
        app_id: str,
        request: WorkflowDraftUpdate,
    ) -> WorkflowDraftRecord:
        return self.store.update_draft(
            app_id=app_id,
            expected_revision=request.expected_revision,
            graph=request.graph,
        )

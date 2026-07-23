from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationRecord,
    WorkflowApplicationUpdate,
    WorkflowDraftRecord,
    WorkflowDraftRunRequest,
    WorkflowDraftRunResponse,
    WorkflowDraftUpdate,
    WorkflowValidationReport,
)
from app.storage import SQLiteWorkflowStore
from app.workflows.compiler import WorkflowCompiler
from app.workflows.validator import WorkflowValidator


class WorkflowService:
    """Application service for the workflow control plane."""

    def __init__(
        self,
        store: SQLiteWorkflowStore | None = None,
        validator: WorkflowValidator | None = None,
        compiler: WorkflowCompiler | None = None,
    ) -> None:
        self.store = store or SQLiteWorkflowStore.from_settings()
        self.validator = validator or WorkflowValidator()
        self.compiler = compiler or WorkflowCompiler(validator=self.validator)

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

    def validate_draft(self, app_id: str) -> WorkflowValidationReport:
        draft = self.get_draft(app_id)
        return self.validator.validate(draft.graph)

    async def run_draft(
        self,
        app_id: str,
        request: WorkflowDraftRunRequest,
    ) -> WorkflowDraftRunResponse:
        draft = self.get_draft(app_id)
        compiled = self.compiler.compile(draft.graph)
        return await compiled.run(
            app_id=app_id,
            draft_revision=draft.revision,
            inputs=request.inputs,
            thread_id=request.thread_id,
        )

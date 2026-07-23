from __future__ import annotations

from app.schemas import (
    WorkflowApplicationCreate,
    WorkflowApplicationRecord,
    WorkflowApplicationUpdate,
    WorkflowDraftRecord,
    WorkflowDraftRunRequest,
    WorkflowDraftRunResponse,
    WorkflowDraftUpdate,
    WorkflowExecutionSource,
    WorkflowPublishRequest,
    WorkflowValidationReport,
    WorkflowVersionRecord,
    WorkflowVersionRollbackRequest,
    WorkflowVersionRollbackResponse,
)
from app.storage import SQLiteWorkflowStore, WorkflowRevisionConflict
from app.workflows.compiler import WorkflowCompiler, WorkflowValidationError
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

    def publish(
        self,
        app_id: str,
        request: WorkflowPublishRequest,
    ) -> WorkflowVersionRecord:
        draft = self.get_draft(app_id)
        if draft.revision != request.expected_revision:
            raise WorkflowRevisionConflict(
                request.expected_revision,
                draft.revision,
            )
        report = self.validator.validate(draft.graph)
        if not report.valid:
            raise WorkflowValidationError(report)
        return self.store.publish_draft(
            app_id=app_id,
            expected_revision=request.expected_revision,
            published_by=request.published_by,
            release_notes=request.release_notes,
        )

    def list_versions(
        self,
        app_id: str,
        limit: int = 20,
    ) -> list[WorkflowVersionRecord]:
        return self.store.list_versions(app_id, limit=limit)

    def get_version(
        self,
        app_id: str,
        version_number: int,
    ) -> WorkflowVersionRecord:
        self.store.require_application(app_id)
        version = self.store.get_version(app_id, version_number)
        if version is None:
            raise KeyError((app_id, version_number))
        return version

    def rollback(
        self,
        app_id: str,
        version_number: int,
        request: WorkflowVersionRollbackRequest,
    ) -> WorkflowVersionRollbackResponse:
        version, draft = self.store.restore_version_to_draft(
            app_id=app_id,
            version_number=version_number,
            expected_revision=request.expected_revision,
        )
        return WorkflowVersionRollbackResponse(
            version=version,
            draft=draft,
            requested_by=request.requested_by,
            reason=request.reason,
        )

    async def run_version(
        self,
        app_id: str,
        version_number: int,
        request: WorkflowDraftRunRequest,
    ) -> WorkflowDraftRunResponse:
        version = self.get_version(app_id, version_number)
        compiled = self.compiler.compile(version.graph)
        result = await compiled.run(
            app_id=app_id,
            draft_revision=version.source_draft_revision,
            inputs=request.inputs,
            thread_id=request.thread_id,
        )
        return result.model_copy(
            update={
                "execution_source": WorkflowExecutionSource.published,
                "version_number": version.version_number,
                "metadata": {
                    **result.metadata,
                    "version_id": version.version_id,
                    "graph_sha256": version.graph_sha256,
                },
            }
        )

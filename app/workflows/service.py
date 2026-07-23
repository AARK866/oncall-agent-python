from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.schemas import (
    HumanReviewDecisionRequest,
    WorkflowApplicationCreate,
    WorkflowApplicationRecord,
    WorkflowApplicationUpdate,
    WorkflowAuditEventRecord,
    WorkflowDraftRecord,
    WorkflowDraftRunRequest,
    WorkflowDraftRunResponse,
    WorkflowDraftUpdate,
    WorkflowExecutionSource,
    WorkflowPublishRequest,
    WorkflowReviewDecisionResponse,
    WorkflowReviewRequestRecord,
    WorkflowReviewStatus,
    WorkflowRunEventRecord,
    WorkflowRunEventType,
    WorkflowRunMetricsResponse,
    WorkflowRunRecord,
    WorkflowRunStatus,
    WorkflowValidationReport,
    WorkflowVersionRecord,
    WorkflowVersionRollbackRequest,
    WorkflowVersionRollbackResponse,
)
from app.security import redact_text
from app.storage import (
    SQLiteWorkflowStore,
    WorkflowReviewConflict,
    WorkflowRevisionConflict,
)
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
        self._compiler = compiler

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
        return await self._start_run(
            app_id=app_id,
            execution_source=WorkflowExecutionSource.draft,
            draft_revision=draft.revision,
            version_number=None,
            graph=draft.graph,
            request=request,
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
            requested_by=request.requested_by,
            reason=request.reason,
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
        return await self._start_run(
            app_id=app_id,
            execution_source=WorkflowExecutionSource.published,
            draft_revision=version.source_draft_revision,
            version_number=version.version_number,
            graph=version.graph,
            request=request,
            metadata={
                "version_id": version.version_id,
                "graph_sha256": version.graph_sha256,
            },
        )

    def list_runs(
        self,
        app_id: str,
        status: WorkflowRunStatus | None = None,
        limit: int = 20,
    ) -> list[WorkflowRunRecord]:
        return self.store.list_runs(app_id, status=status, limit=limit)

    def get_run(self, app_id: str, run_id: str) -> WorkflowRunRecord:
        return self.store.require_run(app_id, run_id)

    def run_events(
        self,
        app_id: str,
        run_id: str,
    ) -> list[WorkflowRunEventRecord]:
        return self.store.list_run_events(app_id, run_id)

    def run_reviews(
        self,
        app_id: str,
        run_id: str,
    ) -> list[WorkflowReviewRequestRecord]:
        return self.store.list_reviews(app_id, run_id)

    def run_metrics(
        self,
        app_id: str,
        window_hours: int = 24,
    ) -> WorkflowRunMetricsResponse:
        return self.store.run_metrics(app_id, window_hours=window_hours)

    def audit_events(
        self,
        app_id: str,
        limit: int = 100,
    ) -> list[WorkflowAuditEventRecord]:
        return self.store.list_audit_events(app_id, limit=limit)

    async def approve_review(
        self,
        app_id: str,
        run_id: str,
        review_id: str,
        request: HumanReviewDecisionRequest,
    ) -> WorkflowReviewDecisionResponse:
        existing = self.store.get_review(app_id, run_id, review_id)
        if existing is None:
            raise KeyError((run_id, review_id))
        current_run = self.store.require_run(app_id, run_id)
        if existing.status == WorkflowReviewStatus.approved:
            if current_run.status != WorkflowRunStatus.waiting_review:
                raise WorkflowReviewConflict(
                    f"Workflow review {review_id} has already been decided."
                )
            review = existing
        else:
            review = self.store.decide_review(
                app_id=app_id,
                run_id=run_id,
                review_id=review_id,
                decision=WorkflowReviewStatus.approved,
                reviewer=request.reviewer,
                reason=request.reason,
            )
        reviews = self.store.list_reviews(app_id, run_id)
        if any(item.status == WorkflowReviewStatus.pending for item in reviews):
            return WorkflowReviewDecisionResponse(
                review=review,
                run=self.store.require_run(app_id, run_id),
            )

        run = self.store.require_run(app_id, run_id)
        resume_value = _review_resume_value(reviews)
        result = await self._resume_run(run, resume_value)
        return WorkflowReviewDecisionResponse(
            review=review,
            run=self.store.require_run(app_id, run_id),
            result=result,
        )

    def reject_review(
        self,
        app_id: str,
        run_id: str,
        review_id: str,
        request: HumanReviewDecisionRequest,
    ) -> WorkflowReviewDecisionResponse:
        existing = self.store.get_review(app_id, run_id, review_id)
        if existing is None:
            raise KeyError((run_id, review_id))
        current_run = self.store.require_run(app_id, run_id)
        if existing.status == WorkflowReviewStatus.rejected:
            if current_run.status != WorkflowRunStatus.waiting_review:
                raise WorkflowReviewConflict(
                    f"Workflow review {review_id} has already been decided."
                )
            review = existing
        else:
            review = self.store.decide_review(
                app_id=app_id,
                run_id=run_id,
                review_id=review_id,
                decision=WorkflowReviewStatus.rejected,
                reviewer=request.reviewer,
                reason=request.reason,
            )
        run = self.store.reject_run(
            app_id=app_id,
            run_id=run_id,
            reviewer=request.reviewer,
            reason=request.reason,
        )
        return WorkflowReviewDecisionResponse(review=review, run=run)

    async def _start_run(
        self,
        app_id: str,
        execution_source: WorkflowExecutionSource,
        draft_revision: int,
        version_number: int | None,
        graph,
        request: WorkflowDraftRunRequest,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowDraftRunResponse:
        thread_id = request.thread_id or f"wfthread_{uuid4().hex}"
        run = self.store.create_run(
            app_id=app_id,
            execution_source=execution_source,
            draft_revision=draft_revision,
            version_number=version_number,
            thread_id=thread_id,
            inputs=request.inputs,
            started_by=request.requested_by,
            graph=graph,
        )
        return await self._invoke_run(
            run=run,
            graph=graph,
            inputs=request.inputs,
            metadata=metadata,
        )

    async def _resume_run(
        self,
        run: WorkflowRunRecord,
        resume_value: Any,
    ) -> WorkflowDraftRunResponse:
        graph = self.store.get_run_graph(run.app_id, run.run_id)
        return await self._invoke_run(
            run=run,
            graph=graph,
            inputs=run.inputs,
            resume_value=resume_value,
        )

    async def _invoke_run(
        self,
        run: WorkflowRunRecord,
        graph,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        resume_value: Any = None,
    ) -> WorkflowDraftRunResponse:
        event_handler = self._event_handler(run.run_id)
        try:
            compiled = self.compiler.compile(graph, event_handler=event_handler)
            run_kwargs = {
                "app_id": run.app_id,
                "draft_revision": run.draft_revision,
                "inputs": inputs,
                "thread_id": run.thread_id,
                "run_id": run.run_id,
            }
            if resume_value is None:
                result = await compiled.run(**run_kwargs)
            else:
                result = await compiled.run(
                    **run_kwargs,
                    resume_value=resume_value,
                )
            result = result.model_copy(
                update={
                    "run_id": run.run_id,
                    "execution_source": run.execution_source,
                    "version_number": run.version_number,
                    "metadata": {
                        **result.metadata,
                        **(metadata or {}),
                        "run_id": run.run_id,
                    },
                }
            )
            self.store.complete_run(run.app_id, run.run_id, result)
            if result.status == WorkflowRunStatus.waiting_review:
                for payload in result.review_requests:
                    node_id = str(payload.get("node_id") or "human_review")
                    self.store.create_review_request(
                        app_id=run.app_id,
                        run_id=run.run_id,
                        node_id=node_id,
                        payload=payload,
                    )
            return result
        except Exception as exc:
            error = redact_text(f"{type(exc).__name__}: {exc}")
            self.store.fail_run(run.app_id, run.run_id, error)
            raise

    def _event_handler(self, run_id: str):
        def record(
            event_type: WorkflowRunEventType,
            node_id: str | None,
            message: str,
            data: dict[str, Any],
        ) -> None:
            sanitized_data = dict(data)
            if "error" in sanitized_data:
                sanitized_data["error"] = redact_text(str(sanitized_data["error"]))
            self.store.append_run_event(
                run_id=run_id,
                event_type=event_type,
                message=message,
                node_id=node_id,
                data=sanitized_data,
            )

        return record

    @property
    def compiler(self) -> WorkflowCompiler:
        if self._compiler is None:
            self._compiler = WorkflowCompiler(validator=self.validator)
        return self._compiler


def _review_resume_value(
    reviews: list[WorkflowReviewRequestRecord],
) -> dict[str, Any]:
    decisions_by_interrupt: dict[str, Any] = {}
    fallback_decision: dict[str, Any] | None = None
    for review in reviews:
        decision = {
            "approved": True,
            "review_id": review.review_id,
            "reviewer": review.reviewer,
            "reason": review.decision_reason,
        }
        fallback_decision = decision
        interrupt_id = review.payload.get("interrupt_id")
        if interrupt_id:
            decisions_by_interrupt[str(interrupt_id)] = decision
    if decisions_by_interrupt:
        return decisions_by_interrupt
    return fallback_decision or {"approved": True}

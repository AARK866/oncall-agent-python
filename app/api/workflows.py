from fastapi import APIRouter, Depends, HTTPException, Query, status

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
    WorkflowPublishRequest,
    WorkflowReviewDecisionResponse,
    WorkflowReviewRequestRecord,
    WorkflowRunEventRecord,
    WorkflowRunMetricsResponse,
    WorkflowRunRecord,
    WorkflowRunStatus,
    WorkflowValidationReport,
    WorkflowVersionRecord,
    WorkflowVersionRollbackRequest,
    WorkflowVersionRollbackResponse,
)
from app.rag.access_control import KnowledgeAccessContext
from app.security import require_api_principal, require_api_token
from app.storage import (
    WorkflowReviewConflict,
    WorkflowRevisionConflict,
    WorkflowRunStateConflict,
)
from app.workflows import (
    WorkflowNodeExecutionError,
    WorkflowService,
    WorkflowValidationError,
)

router = APIRouter(
    prefix="/api/workflow-apps",
    tags=["workflow-apps"],
    dependencies=[Depends(require_api_token)],
)


@router.post(
    "",
    response_model=WorkflowApplicationRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_application(
    request: WorkflowApplicationCreate,
) -> WorkflowApplicationRecord:
    return _service().create(request)


@router.get("", response_model=list[WorkflowApplicationRecord])
async def list_workflow_applications(
    limit: int = Query(default=20, ge=1, le=100),
    include_archived: bool = False,
) -> list[WorkflowApplicationRecord]:
    return _service().list(limit=limit, include_archived=include_archived)


@router.get("/{app_id}", response_model=WorkflowApplicationRecord)
async def get_workflow_application(app_id: str) -> WorkflowApplicationRecord:
    application = _service().get(app_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Workflow application not found")
    return application


@router.patch("/{app_id}", response_model=WorkflowApplicationRecord)
async def update_workflow_application(
    app_id: str,
    request: WorkflowApplicationUpdate,
) -> WorkflowApplicationRecord:
    try:
        return _service().update(app_id, request)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


@router.get("/{app_id}/draft", response_model=WorkflowDraftRecord)
async def get_workflow_draft(app_id: str) -> WorkflowDraftRecord:
    try:
        return _service().get_draft(app_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


@router.put("/{app_id}/draft", response_model=WorkflowDraftRecord)
async def update_workflow_draft(
    app_id: str,
    request: WorkflowDraftUpdate,
) -> WorkflowDraftRecord:
    try:
        return _service().update_draft(app_id, request)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None
    except WorkflowRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from None


@router.post(
    "/{app_id}/draft/validate",
    response_model=WorkflowValidationReport,
)
async def validate_workflow_draft(app_id: str) -> WorkflowValidationReport:
    try:
        return _service().validate_draft(app_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


@router.post(
    "/{app_id}/draft/run",
    response_model=WorkflowDraftRunResponse,
)
async def run_workflow_draft(
    app_id: str,
    request: WorkflowDraftRunRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> WorkflowDraftRunResponse:
    try:
        return await _service().run_draft(
            app_id,
            request.model_copy(
                update={
                    "requested_by": _trusted_actor(
                        principal,
                        request.requested_by,
                    )
                }
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.report.model_dump(mode="json"),
        ) from None
    except WorkflowNodeExecutionError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "node_id": exc.node_id},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post(
    "/{app_id}/publish",
    response_model=WorkflowVersionRecord,
    status_code=status.HTTP_201_CREATED,
)
async def publish_workflow(
    app_id: str,
    request: WorkflowPublishRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> WorkflowVersionRecord:
    try:
        return _service().publish(
            app_id,
            request.model_copy(
                update={
                    "published_by": _trusted_actor(
                        principal,
                        request.published_by,
                    )
                }
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None
    except WorkflowRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from None
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.report.model_dump(mode="json"),
        ) from None


@router.get(
    "/{app_id}/versions",
    response_model=list[WorkflowVersionRecord],
)
async def list_workflow_versions(
    app_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[WorkflowVersionRecord]:
    try:
        return _service().list_versions(app_id, limit=limit)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


@router.get(
    "/{app_id}/versions/{version_number}",
    response_model=WorkflowVersionRecord,
)
async def get_workflow_version(
    app_id: str,
    version_number: int,
) -> WorkflowVersionRecord:
    try:
        return _service().get_version(app_id, version_number)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow version not found",
        ) from None


@router.post(
    "/{app_id}/versions/{version_number}/rollback",
    response_model=WorkflowVersionRollbackResponse,
)
async def rollback_workflow_version(
    app_id: str,
    version_number: int,
    request: WorkflowVersionRollbackRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> WorkflowVersionRollbackResponse:
    try:
        return _service().rollback(
            app_id,
            version_number,
            request.model_copy(
                update={
                    "requested_by": _trusted_actor(
                        principal,
                        request.requested_by,
                    )
                }
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow version not found",
        ) from None
    except WorkflowRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from None


@router.post(
    "/{app_id}/versions/{version_number}/run",
    response_model=WorkflowDraftRunResponse,
)
async def run_workflow_version(
    app_id: str,
    version_number: int,
    request: WorkflowDraftRunRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> WorkflowDraftRunResponse:
    try:
        return await _service().run_version(
            app_id,
            version_number,
            request.model_copy(
                update={
                    "requested_by": _trusted_actor(
                        principal,
                        request.requested_by,
                    )
                }
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow version not found",
        ) from None
    except WorkflowNodeExecutionError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "node_id": exc.node_id},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get(
    "/{app_id}/runs",
    response_model=list[WorkflowRunRecord],
)
async def list_workflow_runs(
    app_id: str,
    run_status: WorkflowRunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[WorkflowRunRecord]:
    try:
        return _service().list_runs(app_id, status=run_status, limit=limit)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


@router.get(
    "/{app_id}/runs/metrics",
    response_model=WorkflowRunMetricsResponse,
)
async def get_workflow_run_metrics(
    app_id: str,
    window_hours: int = Query(default=24, ge=1, le=24 * 90),
) -> WorkflowRunMetricsResponse:
    try:
        return _service().run_metrics(app_id, window_hours=window_hours)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


@router.get(
    "/{app_id}/runs/{run_id}",
    response_model=WorkflowRunRecord,
)
async def get_workflow_run(
    app_id: str,
    run_id: str,
) -> WorkflowRunRecord:
    try:
        return _service().get_run(app_id, run_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow run not found",
        ) from None


@router.get(
    "/{app_id}/runs/{run_id}/events",
    response_model=list[WorkflowRunEventRecord],
)
async def get_workflow_run_events(
    app_id: str,
    run_id: str,
) -> list[WorkflowRunEventRecord]:
    try:
        return _service().run_events(app_id, run_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow run not found",
        ) from None


@router.get(
    "/{app_id}/runs/{run_id}/reviews",
    response_model=list[WorkflowReviewRequestRecord],
)
async def get_workflow_run_reviews(
    app_id: str,
    run_id: str,
) -> list[WorkflowReviewRequestRecord]:
    try:
        return _service().run_reviews(app_id, run_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow run not found",
        ) from None


@router.post(
    "/{app_id}/runs/{run_id}/reviews/{review_id}/approve",
    response_model=WorkflowReviewDecisionResponse,
)
async def approve_workflow_review(
    app_id: str,
    run_id: str,
    review_id: str,
    request: HumanReviewDecisionRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> WorkflowReviewDecisionResponse:
    try:
        return await _service().approve_review(
            app_id,
            run_id,
            review_id,
            request.model_copy(
                update={
                    "reviewer": _trusted_actor(
                        principal,
                        request.reviewer,
                    )
                }
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow run or review not found",
        ) from None
    except (WorkflowReviewConflict, WorkflowRunStateConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except WorkflowNodeExecutionError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "node_id": exc.node_id},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post(
    "/{app_id}/runs/{run_id}/reviews/{review_id}/reject",
    response_model=WorkflowReviewDecisionResponse,
)
async def reject_workflow_review(
    app_id: str,
    run_id: str,
    review_id: str,
    request: HumanReviewDecisionRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> WorkflowReviewDecisionResponse:
    try:
        return _service().reject_review(
            app_id,
            run_id,
            review_id,
            request.model_copy(
                update={
                    "reviewer": _trusted_actor(
                        principal,
                        request.reviewer,
                    )
                }
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow run or review not found",
        ) from None
    except (WorkflowReviewConflict, WorkflowRunStateConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get(
    "/{app_id}/audit-events",
    response_model=list[WorkflowAuditEventRecord],
)
async def list_workflow_audit_events(
    app_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[WorkflowAuditEventRecord]:
    try:
        return _service().audit_events(app_id, limit=limit)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Workflow application not found",
        ) from None


def _service() -> WorkflowService:
    return WorkflowService()


def _trusted_actor(
    principal: KnowledgeAccessContext,
    requested_actor: str,
) -> str:
    if principal.source == "api_token":
        return principal.subject
    return requested_actor

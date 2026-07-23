from fastapi import APIRouter, Depends, HTTPException, Query, status

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
from app.security import require_api_token
from app.storage import WorkflowRevisionConflict
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
) -> WorkflowDraftRunResponse:
    try:
        return await _service().run_draft(app_id, request)
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


def _service() -> WorkflowService:
    return WorkflowService()

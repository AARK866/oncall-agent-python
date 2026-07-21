from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.config import settings
from app.schemas import (
    DiagnosisTaskCancelRequest,
    DiagnosisTaskEventRecord,
    DiagnosisTaskRecord,
    DiagnosisTaskRerunRequest,
    HumanReviewRequestRecord,
    OpsGraphCheckpointRecord,
    StaleTaskRecoveryRequest,
    StaleTaskRecoveryResponse,
)
from app.security import require_api_token
from app.tasks import DiagnosisTaskQueue

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[DiagnosisTaskRecord])
async def list_tasks(limit: int = Query(default=20, ge=1, le=100)) -> list[DiagnosisTaskRecord]:
    return _queue().list(limit=limit)


@router.post(
    "/recover-stale",
    response_model=StaleTaskRecoveryResponse,
)
async def recover_stale_tasks(request: StaleTaskRecoveryRequest) -> StaleTaskRecoveryResponse:
    max_age_seconds = request.max_age_seconds or settings.diagnosis_task_timeout_seconds
    limit = request.limit or settings.diagnosis_task_recovery_limit
    tasks = _queue().recover_stale_tasks(
        requested_by=request.requested_by,
        reason=request.reason,
        max_age_seconds=max_age_seconds,
        limit=limit,
    )
    return StaleTaskRecoveryResponse(
        recovered=len(tasks),
        tasks=tasks,
        metadata={
            "requested_by": request.requested_by,
            "reason": request.reason,
            "max_age_seconds": max_age_seconds,
            "limit": limit,
        },
    )


@router.post(
    "/{task_id}/rerun",
    response_model=DiagnosisTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rerun_task(
    task_id: str,
    request: DiagnosisTaskRerunRequest,
    background_tasks: BackgroundTasks,
) -> DiagnosisTaskRecord:
    queue = _queue()
    try:
        task = queue.rerun(
            task_id=task_id,
            requested_by=request.requested_by,
            reason=request.reason,
            force=request.force,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    background_tasks.add_task(queue.run, task.task_id)
    return task


@router.post(
    "/{task_id}/cancel",
    response_model=DiagnosisTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_task(
    task_id: str,
    request: DiagnosisTaskCancelRequest,
) -> DiagnosisTaskRecord:
    queue = _queue()
    try:
        return queue.cancel(
            task_id=task_id,
            requested_by=request.requested_by,
            reason=request.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/{task_id}", response_model=DiagnosisTaskRecord)
async def get_task(task_id: str) -> DiagnosisTaskRecord:
    task = _queue().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/events", response_model=list[DiagnosisTaskEventRecord])
async def get_task_events(task_id: str) -> list[DiagnosisTaskEventRecord]:
    queue = _queue()
    task = queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return queue.events(task_id)


@router.get("/{task_id}/reruns", response_model=list[DiagnosisTaskRecord])
async def get_task_reruns(task_id: str) -> list[DiagnosisTaskRecord]:
    queue = _queue()
    task = queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return queue.reruns(task_id)


@router.get("/{task_id}/checkpoints", response_model=list[OpsGraphCheckpointRecord])
async def get_task_checkpoints(task_id: str) -> list[OpsGraphCheckpointRecord]:
    queue = _queue()
    task = queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return queue.checkpoints(task_id)


@router.get("/{task_id}/reviews", response_model=list[HumanReviewRequestRecord])
async def get_task_reviews(task_id: str) -> list[HumanReviewRequestRecord]:
    queue = _queue()
    task = queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return queue.human_reviews(task_id)


def _queue() -> DiagnosisTaskQueue:
    return DiagnosisTaskQueue()

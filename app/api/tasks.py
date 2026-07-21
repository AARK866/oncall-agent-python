from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas import (
    DiagnosisTaskEventRecord,
    DiagnosisTaskRecord,
    HumanReviewRequestRecord,
    OpsGraphCheckpointRecord,
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

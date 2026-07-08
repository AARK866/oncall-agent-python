from fastapi import APIRouter, HTTPException, Query

from app.schemas import DiagnosisTaskEventRecord, DiagnosisTaskRecord
from app.tasks import DiagnosisTaskQueue

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


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


def _queue() -> DiagnosisTaskQueue:
    return DiagnosisTaskQueue()

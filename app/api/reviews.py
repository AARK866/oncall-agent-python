from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.schemas import (
    HumanReviewDecisionRequest,
    HumanReviewRequestRecord,
    HumanReviewStatus,
)
from app.security import require_api_token
from app.storage import SQLiteTaskStore
from app.tasks import DiagnosisTaskQueue, TaskDispatcher

router = APIRouter(
    prefix="/api/reviews",
    tags=["reviews"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[HumanReviewRequestRecord])
async def list_human_reviews(
    status: HumanReviewStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[HumanReviewRequestRecord]:
    return _store().list_human_review_requests(status=status, limit=limit)


@router.get("/{review_id}", response_model=HumanReviewRequestRecord)
async def get_human_review(review_id: str) -> HumanReviewRequestRecord:
    review = _store().get_human_review_request(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Human review request not found")
    return review


@router.post("/{review_id}/approve", response_model=HumanReviewRequestRecord)
async def approve_human_review(
    review_id: str,
    request: HumanReviewDecisionRequest,
    background_tasks: BackgroundTasks,
) -> HumanReviewRequestRecord:
    review = _decide(review_id, HumanReviewStatus.approved, request)
    TaskDispatcher().dispatch_diagnosis(review.task_id, background_tasks)
    return review


@router.post("/{review_id}/reject", response_model=HumanReviewRequestRecord)
async def reject_human_review(
    review_id: str,
    request: HumanReviewDecisionRequest,
) -> HumanReviewRequestRecord:
    review = _decide(review_id, HumanReviewStatus.rejected, request)
    DiagnosisTaskQueue().reject_after_review(
        review=review,
        reason=request.reason,
    )
    return review


def _decide(
    review_id: str,
    status: HumanReviewStatus,
    request: HumanReviewDecisionRequest,
) -> HumanReviewRequestRecord:
    store = _store()
    if store.get_human_review_request(review_id) is None:
        raise HTTPException(status_code=404, detail="Human review request not found")
    return store.decide_human_review_request(
        review_id=review_id,
        status=status,
        reviewer=request.reviewer,
        reason=request.reason,
    )


def _store() -> SQLiteTaskStore:
    return SQLiteTaskStore.from_settings()

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.config import settings
from app.rag import KnowledgeBase
from app.rag.ingestion import KnowledgeIngestionPipeline
from app.schemas import (
    KnowledgeDocumentDetail,
    KnowledgeDocumentSummary,
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeIngestionRetryRequest,
    KnowledgeIngestionTaskRecord,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStatsResponse,
)
from app.rag.access_control import KnowledgeAccessContext
from app.security import require_api_principal, require_api_token
from app.tasks import KnowledgeIngestionQueue

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/stats", response_model=KnowledgeStatsResponse)
async def get_knowledge_stats(
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> KnowledgeStatsResponse:
    knowledge_base = _knowledge_base()
    return KnowledgeStatsResponse.model_validate(knowledge_base.stats(principal))


@router.get("/documents", response_model=list[KnowledgeDocumentSummary])
async def list_knowledge_documents(
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> list[KnowledgeDocumentSummary]:
    knowledge_base = _knowledge_base()
    return [
        KnowledgeDocumentSummary.model_validate(document)
        for document in knowledge_base.list_documents(principal)
    ]


@router.get("/documents/{doc_id:path}", response_model=KnowledgeDocumentDetail)
async def get_knowledge_document(
    doc_id: str,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> KnowledgeDocumentDetail:
    knowledge_base = _knowledge_base()
    document = knowledge_base.get_document(doc_id, principal)
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    return KnowledgeDocumentDetail(
        doc_id=document.doc_id,
        title=document.title,
        source=document.source,
        content=document.content,
        metadata=document.metadata,
    )


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    principal: KnowledgeAccessContext = Depends(require_api_principal),
) -> KnowledgeSearchResponse:
    knowledge_base = _knowledge_base()
    results = knowledge_base.search(
        query=request.query,
        top_k=request.top_k,
        service=request.service,
        incident_type=request.incident_type,
        keywords=request.keywords,
        access_context=principal,
    )
    return KnowledgeSearchResponse(
        query=request.query,
        results=results,
        metadata={
            "retrieved_count": len(results),
            "knowledge_base": knowledge_base.stats(principal),
        },
    )


@router.post("/ingest", response_model=KnowledgeIngestResponse)
async def ingest_knowledge(
    request: KnowledgeIngestRequest,
    _: None = Depends(require_api_token),
) -> KnowledgeIngestResponse:
    return await KnowledgeIngestionPipeline().ingest(
        source=request.source,
        path=request.path,
        chunk_size=request.chunk_size,
        chunk_overlap=request.chunk_overlap,
        full_rebuild=request.full_rebuild,
    )


@router.post(
    "/ingestion-tasks",
    response_model=KnowledgeIngestionTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_knowledge_ingestion_task(
    request: KnowledgeIngestRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_token),
) -> KnowledgeIngestionTaskRecord:
    queue = _ingestion_queue()
    task = queue.submit(request)
    background_tasks.add_task(queue.run, task.task_id)
    return task


@router.get(
    "/ingestion-tasks",
    response_model=list[KnowledgeIngestionTaskRecord],
)
async def list_knowledge_ingestion_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_api_token),
) -> list[KnowledgeIngestionTaskRecord]:
    return _ingestion_queue().list(limit=limit)


@router.get(
    "/ingestion-tasks/{task_id}",
    response_model=KnowledgeIngestionTaskRecord,
)
async def get_knowledge_ingestion_task(
    task_id: str,
    _: None = Depends(require_api_token),
) -> KnowledgeIngestionTaskRecord:
    task = _ingestion_queue().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Knowledge ingestion task not found")
    return task


@router.post(
    "/ingestion-tasks/{task_id}/retry",
    response_model=KnowledgeIngestionTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_knowledge_ingestion_task(
    task_id: str,
    request: KnowledgeIngestionRetryRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_token),
) -> KnowledgeIngestionTaskRecord:
    queue = _ingestion_queue()
    try:
        task = queue.retry(task_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Knowledge ingestion task not found",
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    background_tasks.add_task(queue.run, task.task_id)
    return task


def _knowledge_base() -> KnowledgeBase:
    return KnowledgeBase.from_directory(
        settings.knowledge_local_path,
        chunk_size=settings.knowledge_ingest_chunk_size,
        chunk_overlap=settings.knowledge_ingest_chunk_overlap,
    )


def _ingestion_queue() -> KnowledgeIngestionQueue:
    return KnowledgeIngestionQueue()

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.rag import KnowledgeBase
from app.rag.ingestion import KnowledgeIngestionPipeline
from app.schemas import (
    KnowledgeDocumentDetail,
    KnowledgeDocumentSummary,
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStatsResponse,
)
from app.rag.access_control import KnowledgeAccessContext
from app.security import require_api_principal, require_api_token

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
    )


def _knowledge_base() -> KnowledgeBase:
    return KnowledgeBase.from_directory(
        settings.knowledge_local_path,
        chunk_size=settings.knowledge_ingest_chunk_size,
        chunk_overlap=settings.knowledge_ingest_chunk_overlap,
    )

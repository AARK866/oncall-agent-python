from fastapi import APIRouter, HTTPException

from app.rag import KnowledgeBase
from app.schemas import (
    KnowledgeDocumentDetail,
    KnowledgeDocumentSummary,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStatsResponse,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

knowledge_base = KnowledgeBase.from_directory("app/data/runbooks")


@router.get("/stats", response_model=KnowledgeStatsResponse)
async def get_knowledge_stats() -> KnowledgeStatsResponse:
    return KnowledgeStatsResponse.model_validate(knowledge_base.stats())


@router.get("/documents", response_model=list[KnowledgeDocumentSummary])
async def list_knowledge_documents() -> list[KnowledgeDocumentSummary]:
    return [
        KnowledgeDocumentSummary.model_validate(document)
        for document in knowledge_base.list_documents()
    ]


@router.get("/documents/{doc_id:path}", response_model=KnowledgeDocumentDetail)
async def get_knowledge_document(doc_id: str) -> KnowledgeDocumentDetail:
    document = knowledge_base.get_document(doc_id)
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
async def search_knowledge(request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    results = knowledge_base.search(
        query=request.query,
        top_k=request.top_k,
        service=request.service,
        incident_type=request.incident_type,
        keywords=request.keywords,
    )
    return KnowledgeSearchResponse(
        query=request.query,
        results=results,
        metadata={
            "retrieved_count": len(results),
            "knowledge_base": knowledge_base.stats(),
        },
    )

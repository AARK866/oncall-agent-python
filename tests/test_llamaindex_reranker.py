from app.rag import LlamaIndexReranker
from app.schemas import SourceDocument


def test_llamaindex_reranker_promotes_query_relevant_evidence() -> None:
    reranker = LlamaIndexReranker(vector_weight=0.3, lexical_weight=0.7)
    candidates = [
        _source(
            doc_id="generic.md#chunk-0",
            content="General service deployment checklist.",
            score=0.95,
            retriever="vector",
        ),
        _source(
            doc_id="payment.md#chunk-0",
            content="Payment API 5xx caused by an exhausted database connection pool.",
            score=0.72,
            retriever="vector",
        ),
    ]

    results = reranker.rerank(
        query="payment 5xx database connection pool",
        candidates=candidates,
        top_k=1,
    )

    assert len(results) == 1
    assert results[0].doc_id == "payment.md#chunk-0"
    assert results[0].metadata["retrieval_score"] == 0.72
    assert results[0].metadata["candidate_rank"] == 2
    assert results[0].metadata["rerank_rank"] == 1
    assert results[0].metadata["reranker"] == "llamaindex"
    assert results[0].metadata["llamaindex_reranker_native"] is True


def _source(
    doc_id: str,
    content: str,
    score: float,
    retriever: str,
) -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        title=doc_id,
        content=content,
        source=f"app/data/runbooks/{doc_id}",
        score=score,
        metadata={"retriever": retriever},
    )

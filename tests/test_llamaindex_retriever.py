from typing import Any

from app.rag import LlamaIndexReranker, LlamaIndexRetrieverAdapter
from app.schemas import SourceDocument


def test_llamaindex_retriever_preserves_query_filter_score_and_trace() -> None:
    store = FakeVectorStore()
    retriever = LlamaIndexRetrieverAdapter(store)

    results = retriever.search(
        query="payment 5xx",
        top_k=2,
        metadata_filter={"services": "payment-api"},
    )

    assert store.last_request == {
        "query": "payment 5xx",
        "top_k": 2,
        "metadata_filter": {"services": "payment-api"},
    }
    assert len(results) == 1
    assert results[0].doc_id == "payment.md#chunk-0"
    assert results[0].score == 0.91
    assert results[0].metadata["services"] == ["payment-api"]
    assert results[0].metadata["retriever"] == "llamaindex"
    assert results[0].metadata["retriever_backend"] == "fake-vector"
    assert results[0].metadata["llamaindex_native"] is True


def test_llamaindex_retriever_overfetches_before_reranking() -> None:
    store = FakeVectorStore()
    retriever = LlamaIndexRetrieverAdapter(
        store,
        reranker=LlamaIndexReranker(vector_weight=0.3, lexical_weight=0.7),
        candidate_multiplier=3,
    )

    results = retriever.search(query="payment 5xx", top_k=2)

    assert store.last_request is not None
    assert store.last_request["top_k"] == 6
    assert len(results) == 1
    assert results[0].metadata["reranker"] == "llamaindex"


class FakeVectorStore:
    def __init__(self) -> None:
        self.last_request: dict[str, Any] | None = None

    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SourceDocument]:
        self.last_request = {
            "query": query,
            "top_k": top_k,
            "metadata_filter": metadata_filter,
        }
        return [
            SourceDocument(
                doc_id="payment.md#chunk-0",
                title="Payment Runbook",
                content="Check payment-api 5xx and database pool.",
                source="app/data/runbooks/payment.md",
                score=0.91,
                metadata={
                    "doc_id": "payment.md",
                    "services": ["payment-api"],
                    "retriever": "fake-vector",
                },
            )
        ]

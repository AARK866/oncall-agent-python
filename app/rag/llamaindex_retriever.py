from __future__ import annotations

from typing import Any, Protocol

from app.rag.llamaindex_adapter import LlamaIndexAdapter, create_llamaindex_adapter
from app.rag.llamaindex_reranker import LlamaIndexReranker
from app.schemas import SourceDocument


class SearchableVectorStore(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SourceDocument]: ...


class LlamaIndexRetrieverAdapter:
    """Run the existing vector backend through LlamaIndex's retriever contract."""

    def __init__(
        self,
        vector_store: SearchableVectorStore,
        adapter: LlamaIndexAdapter | None = None,
        reranker: LlamaIndexReranker | None = None,
        candidate_multiplier: int = 1,
    ) -> None:
        self.vector_store = vector_store
        self.adapter = adapter or create_llamaindex_adapter()
        self.reranker = reranker
        self.candidate_multiplier = max(candidate_multiplier, 1)

    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SourceDocument]:
        candidate_k = top_k * self.candidate_multiplier if self.reranker else top_k
        if self.adapter.available:
            retriever = _native_retriever(
                vector_store=self.vector_store,
                adapter=self.adapter,
                top_k=candidate_k,
                metadata_filter=metadata_filter,
            )
            nodes = retriever.retrieve(query)
            results = [self.adapter.source_from_node(node) for node in nodes]
            traced = [_with_retrieval_trace(result, native=True) for result in results]
            return self._rerank(query, traced, top_k)

        results = self.vector_store.search(
            query=query,
            top_k=candidate_k,
            metadata_filter=metadata_filter,
        )
        traced = [_with_retrieval_trace(result, native=False) for result in results]
        return self._rerank(query, traced, top_k)

    def describe(self) -> dict[str, Any]:
        return {
            "engine": "llamaindex",
            "native": self.adapter.available,
            "backend": type(self.vector_store).__name__,
            "reranker": self.reranker.describe() if self.reranker else None,
            "candidate_multiplier": self.candidate_multiplier,
        }

    def _rerank(
        self,
        query: str,
        results: list[SourceDocument],
        top_k: int,
    ) -> list[SourceDocument]:
        if self.reranker is None:
            return results[:top_k]
        return self.reranker.rerank(query=query, candidates=results, top_k=top_k)


def _native_retriever(
    vector_store: SearchableVectorStore,
    adapter: LlamaIndexAdapter,
    top_k: int,
    metadata_filter: dict[str, Any] | None,
) -> Any:
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.schema import NodeWithScore, QueryBundle

    class ProjectVectorStoreRetriever(BaseRetriever):
        def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            sources = vector_store.search(
                query=query_bundle.query_str,
                top_k=top_k,
                metadata_filter=metadata_filter,
            )
            return [
                NodeWithScore(
                    node=adapter.node_from_source(source),
                    score=source.score,
                )
                for source in sources
            ]

    return ProjectVectorStoreRetriever()


def _with_retrieval_trace(source: SourceDocument, native: bool) -> SourceDocument:
    backend = source.metadata.get("retriever")
    return source.model_copy(
        update={
            "metadata": {
                **source.metadata,
                "retriever": "llamaindex",
                "retriever_backend": backend,
                "llamaindex_native": native,
            }
        }
    )

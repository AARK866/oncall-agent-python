from __future__ import annotations

from typing import Any

from app.rag.llamaindex_adapter import LlamaIndexAdapter, create_llamaindex_adapter
from app.rag.text_features import tokenize_text
from app.schemas import SourceDocument


class LlamaIndexReranker:
    """Rerank retrieved evidence through LlamaIndex's node postprocessor contract."""

    def __init__(
        self,
        vector_weight: float = 0.7,
        lexical_weight: float = 0.3,
        adapter: LlamaIndexAdapter | None = None,
    ) -> None:
        if vector_weight < 0 or lexical_weight < 0:
            raise ValueError("Rerank weights must be non-negative.")
        if vector_weight + lexical_weight <= 0:
            raise ValueError("At least one rerank weight must be greater than zero.")

        total = vector_weight + lexical_weight
        self.vector_weight = vector_weight / total
        self.lexical_weight = lexical_weight / total
        self.adapter = adapter or create_llamaindex_adapter()

    def rerank(
        self,
        query: str,
        candidates: list[SourceDocument],
        top_k: int,
    ) -> list[SourceDocument]:
        prepared = [
            candidate.model_copy(
                update={
                    "metadata": {
                        **candidate.metadata,
                        "retrieval_score": candidate.score,
                        "candidate_rank": rank,
                    }
                }
            )
            for rank, candidate in enumerate(candidates, start=1)
        ]

        if self.adapter.available:
            return self._native_rerank(query, prepared, top_k)
        return self._fallback_rerank(query, prepared, top_k)

    def describe(self) -> dict[str, Any]:
        return {
            "name": "llamaindex",
            "native": self.adapter.available,
            "vector_weight": self.vector_weight,
            "lexical_weight": self.lexical_weight,
        }

    def _native_rerank(
        self,
        query: str,
        candidates: list[SourceDocument],
        top_k: int,
    ) -> list[SourceDocument]:
        from llama_index.core.schema import NodeWithScore

        nodes = [
            NodeWithScore(
                node=self.adapter.node_from_source(candidate),
                score=candidate.score,
            )
            for candidate in candidates
        ]
        postprocessor = _native_postprocessor(
            top_k=top_k,
            vector_weight=self.vector_weight,
            lexical_weight=self.lexical_weight,
        )
        reranked = postprocessor.postprocess_nodes(nodes, query_str=query)
        return [self.adapter.source_from_node(node) for node in reranked]

    def _fallback_rerank(
        self,
        query: str,
        candidates: list[SourceDocument],
        top_k: int,
    ) -> list[SourceDocument]:
        scored = [
            _reranked_source(
                source=candidate,
                score=_combined_score(
                    query=query,
                    text=f"{candidate.title}\n{candidate.content}",
                    vector_score=candidate.score,
                    vector_weight=self.vector_weight,
                    lexical_weight=self.lexical_weight,
                ),
            )
            for candidate in candidates
        ]
        scored.sort(key=lambda item: item.score or 0.0, reverse=True)
        return [
            source.model_copy(
                update={
                    "metadata": {
                        **source.metadata,
                        "rerank_rank": rank,
                        "llamaindex_reranker_native": False,
                    }
                }
            )
            for rank, source in enumerate(scored[:top_k], start=1)
        ]


def _native_postprocessor(
    top_k: int,
    vector_weight: float,
    lexical_weight: float,
) -> Any:
    from llama_index.core.postprocessor.types import BaseNodePostprocessor
    from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle

    class RelevancePostprocessor(BaseNodePostprocessor):
        top_k: int
        vector_weight: float
        lexical_weight: float

        def _postprocess_nodes(
            self,
            nodes: list[NodeWithScore],
            query_bundle: QueryBundle | None = None,
        ) -> list[NodeWithScore]:
            query = query_bundle.query_str if query_bundle else ""
            for node in nodes:
                retrieval_score = node.score
                node.score = _combined_score(
                    query=query,
                    text=node.get_content(metadata_mode=MetadataMode.NONE),
                    vector_score=retrieval_score,
                    vector_weight=self.vector_weight,
                    lexical_weight=self.lexical_weight,
                )
                node.metadata.update(
                    {
                        "reranker": "llamaindex",
                        "rerank_score": node.score,
                        "llamaindex_reranker_native": True,
                    }
                )

            nodes.sort(key=lambda item: item.score or 0.0, reverse=True)
            selected = nodes[: self.top_k]
            for rank, node in enumerate(selected, start=1):
                node.metadata["rerank_rank"] = rank
            return selected

    return RelevancePostprocessor(
        top_k=top_k,
        vector_weight=vector_weight,
        lexical_weight=lexical_weight,
    )


def _combined_score(
    query: str,
    text: str,
    vector_score: float | None,
    vector_weight: float,
    lexical_weight: float,
) -> float:
    query_tokens = tokenize_text(query)
    text_tokens = tokenize_text(text)
    lexical_score = len(query_tokens & text_tokens) / len(query_tokens) if query_tokens else 0.0
    normalized_vector_score = max(0.0, min(1.0, vector_score or 0.0))
    return round(
        normalized_vector_score * vector_weight + lexical_score * lexical_weight,
        6,
    )


def _reranked_source(source: SourceDocument, score: float) -> SourceDocument:
    return source.model_copy(
        update={
            "score": score,
            "metadata": {
                **source.metadata,
                "reranker": "llamaindex",
                "rerank_score": score,
            },
        }
    )

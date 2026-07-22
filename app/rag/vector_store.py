import math
from dataclasses import dataclass
from typing import Any

from app.rag.embeddings import EmbeddingModel, HashEmbeddingModel
from app.rag.access_control import KnowledgeAccessContext, can_access_document
from app.rag.filters import matches_metadata
from app.rag.splitter import DocumentChunk
from app.schemas import SourceDocument


@dataclass(frozen=True)
class VectorEntry:
    chunk: DocumentChunk
    vector: list[float]


class InMemoryVectorStore:
    """In-memory vector store used before introducing Milvus."""

    def __init__(
        self,
        entries: list[VectorEntry],
        embedding_model: EmbeddingModel | None = None,
    ) -> None:
        self.entries = entries
        self.embedding_model = embedding_model or HashEmbeddingModel()

    @classmethod
    def from_chunks(
        cls,
        chunks: list[DocumentChunk],
        embedding_model: EmbeddingModel | None = None,
    ) -> "InMemoryVectorStore":
        model = embedding_model or HashEmbeddingModel()
        entries = [
            VectorEntry(
                chunk=chunk,
                vector=model.embed(f"{chunk.title}\n{chunk.content}"),
            )
            for chunk in chunks
        ]
        return cls(entries=entries, embedding_model=model)

    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
        access_context: KnowledgeAccessContext | None = None,
    ) -> list[SourceDocument]:
        query_vector = self.embedding_model.embed(query)
        if not any(query_vector) and not metadata_filter:
            return []

        scored_entries: list[tuple[float, VectorEntry]] = []
        for entry in self.entries:
            if not can_access_document(entry.chunk.metadata, access_context):
                continue
            if metadata_filter and not matches_metadata(entry.chunk.metadata, metadata_filter):
                continue

            score = _cosine_similarity(query_vector, entry.vector)
            if score > 0 or metadata_filter:
                scored_entries.append((score, entry))

        scored_entries.sort(key=lambda item: item[0], reverse=True)
        return [
            SourceDocument(
                doc_id=entry.chunk.chunk_id,
                title=entry.chunk.title,
                content=entry.chunk.content,
                source=entry.chunk.source,
                score=round(score, 4),
                metadata={**entry.chunk.metadata, "retriever": "vector"},
            )
            for score, entry in scored_entries[:top_k]
        ]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    numerator = sum(left_value * right_value for left_value, right_value in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)

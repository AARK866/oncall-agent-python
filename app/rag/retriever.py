import math
from typing import Any
from pathlib import Path

from app.rag.document_loader import load_enterprise_documents
from app.rag.access_control import KnowledgeAccessContext, can_access_document
from app.rag.filters import matches_metadata
from app.rag.splitter import DocumentChunk, split_documents
from app.rag.text_features import tokenize_text
from app.schemas import SourceDocument


class LocalKnowledgeBase:
    """A tiny local retriever used before introducing embeddings and Milvus."""

    def __init__(self, chunks: list[DocumentChunk]) -> None:
        self._chunks = chunks
        self._chunk_tokens = {chunk.chunk_id: _tokenize(f"{chunk.title}\n{chunk.content}") for chunk in chunks}

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
    ) -> "LocalKnowledgeBase":
        documents = load_enterprise_documents(directory)
        chunks = split_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return cls(chunks=chunks)

    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
        access_context: KnowledgeAccessContext | None = None,
    ) -> list[SourceDocument]:
        query_tokens = _tokenize(query)
        if not query_tokens and not metadata_filter:
            return []

        scored_chunks: list[tuple[float, DocumentChunk]] = []
        for chunk in self._chunks:
            if not can_access_document(chunk.metadata, access_context):
                continue
            if metadata_filter and not matches_metadata(chunk.metadata, metadata_filter):
                continue
            score = _score(query_tokens, self._chunk_tokens[chunk.chunk_id]) if query_tokens else 0.0
            if score > 0 or metadata_filter:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda item: item[0], reverse=True)
        return [
            SourceDocument(
                doc_id=chunk.chunk_id,
                title=chunk.title,
                content=chunk.content,
                source=chunk.source,
                score=round(score, 4),
                metadata=chunk.metadata,
            )
            for score, chunk in scored_chunks[:top_k]
        ]


def _tokenize(text: str) -> set[str]:
    return tokenize_text(text)


def _score(query_tokens: set[str], document_tokens: set[str]) -> float:
    overlap = query_tokens & document_tokens
    if not overlap:
        return 0.0
    return len(overlap) / math.sqrt(len(query_tokens) * len(document_tokens))

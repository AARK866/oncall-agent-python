import math
import re
from typing import Any
from pathlib import Path

from app.rag.document_loader import load_markdown_documents
from app.rag.splitter import DocumentChunk, split_documents
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
        documents = load_markdown_documents(directory)
        chunks = split_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return cls(chunks=chunks)

    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SourceDocument]:
        query_tokens = _tokenize(query)
        if not query_tokens and not metadata_filter:
            return []

        scored_chunks: list[tuple[float, DocumentChunk]] = []
        for chunk in self._chunks:
            if metadata_filter and not _matches_metadata(chunk.metadata, metadata_filter):
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
    normalized = text.lower()
    ascii_tokens = set(re.findall(r"[a-z0-9_][a-z0-9_.-]*", normalized))
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    chinese_bigrams = {f"{chinese_chars[index]}{chinese_chars[index + 1]}" for index in range(len(chinese_chars) - 1)}
    return ascii_tokens | set(chinese_chars) | chinese_bigrams


def _score(query_tokens: set[str], document_tokens: set[str]) -> float:
    overlap = query_tokens & document_tokens
    if not overlap:
        return 0.0
    return len(overlap) / math.sqrt(len(query_tokens) * len(document_tokens))


def _matches_metadata(metadata: dict[str, Any], metadata_filter: dict[str, Any]) -> bool:
    for key, expected in metadata_filter.items():
        if expected is None:
            continue

        actual = metadata.get(key)
        if actual is None:
            return False

        actual_values = _as_normalized_set(actual)
        expected_values = _as_normalized_set(expected)
        if actual_values.isdisjoint(expected_values):
            return False

    return True


def _as_normalized_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.lower()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).lower() for item in value}
    return {str(value).lower()}

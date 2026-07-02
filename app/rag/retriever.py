import math
import re
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

    def search(self, query: str, top_k: int = 3) -> list[SourceDocument]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored_chunks: list[tuple[float, DocumentChunk]] = []
        for chunk in self._chunks:
            score = _score(query_tokens, self._chunk_tokens[chunk.chunk_id])
            if score > 0:
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

from dataclasses import dataclass, field
from typing import Any

from app.rag.document_loader import RawDocument


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    doc_id: str
    title: str
    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


def split_documents(
    documents: list[RawDocument],
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []

    for document in documents:
        sections = _split_by_markdown_headings(document.content)
        chunk_index = 0
        for section in sections:
            for content in _split_text(section, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
                if not content.strip():
                    continue
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{document.doc_id}#chunk-{chunk_index}",
                        doc_id=document.doc_id,
                        title=document.title,
                        content=content.strip(),
                        source=document.source,
                        metadata={**document.metadata, "chunk_index": chunk_index},
                    )
                )
                chunk_index += 1

    return chunks


def _split_by_markdown_headings(content: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []

    for line in content.splitlines():
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)

    if current:
        sections.append("\n".join(current).strip())

    return sections


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - chunk_overlap, start + 1)

    return chunks

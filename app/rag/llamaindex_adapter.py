from __future__ import annotations

from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import Any

from app.rag.document_loader import RawDocument
from app.rag.splitter import DocumentChunk
from app.schemas import SourceDocument


@dataclass(frozen=True)
class LlamaIndexDocumentSnapshot:
    """Small fallback shape used when llama-index-core is not installed."""

    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlamaIndexNodeSnapshot:
    """Stable node shape mirroring the fields this project needs from LlamaIndex."""

    node_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlamaIndexIngestionBatch:
    """Prepared LlamaIndex objects plus chunks normalized from their nodes."""

    documents: list[Any]
    nodes: list[Any]
    chunks: list[DocumentChunk]


class LlamaIndexAdapter:
    """Bridge between project RAG models and LlamaIndex document/node shapes."""

    def __init__(self) -> None:
        self._document_cls, self._text_node_cls = _llamaindex_classes()

    @property
    def available(self) -> bool:
        return self._document_cls is not None and self._text_node_cls is not None

    def describe(self) -> dict[str, Any]:
        return {
            "engine": "llamaindex",
            "available": self.available,
            "document_class": _class_name(self._document_cls),
            "node_class": _class_name(self._text_node_cls),
        }

    def prepare_ingestion(
        self,
        documents: list[RawDocument],
        chunks: list[DocumentChunk],
    ) -> LlamaIndexIngestionBatch:
        llama_documents = self.documents_from_raw(documents)
        llama_nodes = self.nodes_from_chunks(chunks)
        return LlamaIndexIngestionBatch(
            documents=llama_documents,
            nodes=llama_nodes,
            chunks=self.chunks_from_nodes(llama_nodes),
        )

    def documents_from_raw(self, documents: list[RawDocument]) -> list[Any]:
        return [self.document_from_raw(document) for document in documents]

    def document_from_raw(self, document: RawDocument) -> Any:
        metadata = {
            **document.metadata,
            "doc_id": document.doc_id,
            "title": document.title,
            "source": document.source,
        }
        if self._document_cls is None:
            return LlamaIndexDocumentSnapshot(
                doc_id=document.doc_id,
                text=document.content,
                metadata=metadata,
            )

        return self._document_cls(
            text=document.content,
            metadata=metadata,
            id_=document.doc_id,
        )

    def nodes_from_chunks(self, chunks: list[DocumentChunk]) -> list[Any]:
        return [self.node_from_chunk(chunk) for chunk in chunks]

    def node_from_chunk(self, chunk: DocumentChunk) -> Any:
        metadata = {
            **chunk.metadata,
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "title": chunk.title,
            "source": chunk.source,
            "knowledge_engine": "llamaindex",
        }
        if self._text_node_cls is None:
            return LlamaIndexNodeSnapshot(
                node_id=chunk.chunk_id,
                text=chunk.content,
                metadata=metadata,
            )

        return self._text_node_cls(
            id_=chunk.chunk_id,
            text=chunk.content,
            metadata=metadata,
        )

    def chunks_from_nodes(self, nodes: list[Any]) -> list[DocumentChunk]:
        return [self.chunk_from_node(node) for node in nodes]

    def chunk_from_node(self, node: Any) -> DocumentChunk:
        metadata = _node_metadata(node)
        node_id = _node_id(node, metadata)
        return DocumentChunk(
            chunk_id=str(metadata.get("chunk_id") or node_id),
            doc_id=str(metadata.get("doc_id") or node_id),
            title=str(metadata.get("title") or node_id),
            content=_node_text(node),
            source=str(metadata.get("source") or ""),
            metadata=metadata,
        )

    def source_from_node(self, node: Any, score: float | None = None) -> SourceDocument:
        metadata = _node_metadata(node)
        node_id = _node_id(node, metadata)
        return SourceDocument(
            doc_id=node_id,
            title=str(metadata.get("title") or node_id),
            content=_node_text(node),
            source=str(metadata.get("source") or "") or None,
            score=score if score is not None else _node_score(node),
            metadata=metadata,
        )


def is_llamaindex_available() -> bool:
    try:
        return find_spec("llama_index.core") is not None
    except ModuleNotFoundError:
        return False


def create_llamaindex_adapter() -> LlamaIndexAdapter:
    return LlamaIndexAdapter()


def _llamaindex_classes() -> tuple[type[Any] | None, type[Any] | None]:
    if not is_llamaindex_available():
        return None, None

    from llama_index.core import Document
    from llama_index.core.schema import TextNode

    return Document, TextNode


def _class_name(value: type[Any] | None) -> str | None:
    return f"{value.__module__}.{value.__name__}" if value else None


def _node_metadata(node: Any) -> dict[str, Any]:
    metadata = getattr(node, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _node_id(node: Any, metadata: dict[str, Any]) -> str:
    for attr in ("node_id", "id_", "id"):
        value = getattr(node, attr, None)
        if value:
            return str(value)

    for key in ("chunk_id", "doc_id"):
        value = metadata.get(key)
        if value:
            return str(value)

    return "unknown"


def _node_text(node: Any) -> str:
    get_content = getattr(node, "get_content", None)
    if callable(get_content):
        return str(get_content())

    text = getattr(node, "text", None)
    return str(text or "")


def _node_score(node: Any) -> float | None:
    value = getattr(node, "score", None)
    return float(value) if isinstance(value, int | float) else None

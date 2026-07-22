from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.document_loader import RawDocument, load_markdown_documents
from app.rag.embeddings import create_embedding_model
from app.rag.llamaindex_adapter import create_llamaindex_adapter
from app.rag.llamaindex_retriever import LlamaIndexRetrieverAdapter
from app.rag.llamaindex_reranker import LlamaIndexReranker
from app.rag.milvus_store import MilvusVectorStore
from app.rag.retriever import LocalKnowledgeBase
from app.rag.splitter import DocumentChunk, split_documents
from app.rag.vector_store import InMemoryVectorStore
from app.schemas import SourceDocument


SERVICE_ALIASES: dict[str, set[str]] = {
    "payment-api": {"payment", "payment-api", "pay", "支付"},
    "order-api": {"order", "order-api", "订单"},
}

INCIDENT_TYPE_ALIASES: dict[str, set[str]] = {
    "5xx": {"5xx", "http_5xx", "server error"},
    "timeout": {"timeout", "latency", "slow", "延迟", "超时"},
    "database": {"database", "db", "mysql", "connection pool", "pool exhausted", "数据库", "连接池"},
    "deployment": {"deploy", "deployment", "release", "rollback", "发布", "回滚", "版本"},
}


class KnowledgeBase:
    """Service layer for runbook loading, metadata enrichment, and retrieval."""

    def __init__(
        self,
        documents: list[RawDocument],
        chunks: list[DocumentChunk],
        retriever_mode: str = "keyword",
    ) -> None:
        self.documents = documents
        self.chunks = chunks
        self.retriever_mode = retriever_mode
        self._documents_by_id = {document.doc_id: document for document in documents}
        self._keyword_retriever = LocalKnowledgeBase(chunks=chunks)
        self._vector_store: Any | None = None
        self._llamaindex_retriever: LlamaIndexRetrieverAdapter | None = None

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        retriever_mode: str | None = None,
    ) -> "KnowledgeBase":
        raw_documents = load_markdown_documents(directory)
        documents = enrich_documents_metadata(raw_documents)
        chunks = split_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return cls(
            documents=documents,
            chunks=chunks,
            retriever_mode=retriever_mode or settings.knowledge_retriever_mode,
        )

    def search(
        self,
        query: str,
        top_k: int = 3,
        service: str | None = None,
        incident_type: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[SourceDocument]:
        metadata_filter: dict[str, Any] = {}
        if service:
            metadata_filter["services"] = _canonical_value(service, SERVICE_ALIASES)
        if incident_type:
            metadata_filter["incident_types"] = _canonical_value(incident_type, INCIDENT_TYPE_ALIASES)

        built_query = _build_query(query=query, keywords=keywords)
        mode = self.retriever_mode.strip().lower()

        if mode == "keyword":
            return self._keyword_search(built_query, top_k, metadata_filter or None)
        if mode == "vector":
            try:
                return self._vector_search(built_query, top_k, metadata_filter or None)
            except Exception as exc:
                return self._fallback_keyword_search(
                    query=built_query,
                    top_k=top_k,
                    metadata_filter=metadata_filter or None,
                    fallback_from="vector",
                    error=exc,
                )
        if mode == "hybrid":
            try:
                return self._hybrid_search(built_query, top_k, metadata_filter or None)
            except Exception as exc:
                return self._fallback_keyword_search(
                    query=built_query,
                    top_k=top_k,
                    metadata_filter=metadata_filter or None,
                    fallback_from="hybrid",
                    error=exc,
                )

        raise ValueError(f"Unsupported KNOWLEDGE_RETRIEVER_MODE: {self.retriever_mode}")

    def get_document(self, doc_id: str) -> RawDocument | None:
        return self._documents_by_id.get(doc_id)

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "doc_id": document.doc_id,
                "title": document.title,
                "source": document.source,
                "metadata": document.metadata,
            }
            for document in self.documents
        ]

    def stats(self) -> dict[str, Any]:
        adapter = create_llamaindex_adapter()
        return {
            "document_count": len(self.documents),
            "chunk_count": len(self.chunks),
            "knowledge_engine": settings.knowledge_engine,
            "reranker": settings.knowledge_reranker,
            "rerank_candidate_multiplier": settings.knowledge_rerank_candidate_multiplier,
            "retriever_mode": self.retriever_mode,
            "vector_store": settings.knowledge_vector_store,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "llamaindex": adapter.describe(),
            "services": _collect_metadata_values(self.documents, "services"),
            "incident_types": _collect_metadata_values(self.documents, "incident_types"),
        }

    def _keyword_search(
        self,
        query: str,
        top_k: int,
        metadata_filter: dict[str, Any] | None,
    ) -> list[SourceDocument]:
        results = self._keyword_retriever.search(
            query=query,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )
        return [_with_retriever(result, "keyword") for result in results]

    def _vector_search(
        self,
        query: str,
        top_k: int,
        metadata_filter: dict[str, Any] | None,
    ) -> list[SourceDocument]:
        store = self._get_vector_store()
        engine = settings.knowledge_engine.strip().lower()
        if engine == "llamaindex":
            if self._llamaindex_retriever is None:
                self._llamaindex_retriever = LlamaIndexRetrieverAdapter(
                    store,
                    reranker=_create_reranker(),
                    candidate_multiplier=settings.knowledge_rerank_candidate_multiplier,
                )
            return self._llamaindex_retriever.search(
                query=query,
                top_k=top_k,
                metadata_filter=metadata_filter,
            )
        if engine not in {"local", "", "default"}:
            raise ValueError(f"Unsupported KNOWLEDGE_ENGINE: {settings.knowledge_engine}")

        return store.search(
            query=query,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )

    def _get_vector_store(self):
        if self._vector_store is not None:
            return self._vector_store

        embedding_model = create_embedding_model()
        store_mode = settings.knowledge_vector_store.lower().strip()
        if store_mode in {"in_memory", "memory", "local"}:
            self._vector_store = InMemoryVectorStore.from_chunks(
                chunks=self.chunks,
                embedding_model=embedding_model,
            )
            return self._vector_store

        if store_mode == "milvus":
            self._vector_store = MilvusVectorStore.from_chunks(
                chunks=self.chunks,
                embedding_model=embedding_model,
            )
            return self._vector_store

        raise ValueError(f"Unsupported KNOWLEDGE_VECTOR_STORE: {settings.knowledge_vector_store}")

    def _hybrid_search(
        self,
        query: str,
        top_k: int,
        metadata_filter: dict[str, Any] | None,
    ) -> list[SourceDocument]:
        keyword_results = self._keyword_search(query, top_k, metadata_filter)
        vector_results = self._vector_search(query, top_k, metadata_filter)
        merged: dict[str, SourceDocument] = {}

        for result in vector_results:
            merged[result.doc_id] = result

        for result in keyword_results:
            existing = merged.get(result.doc_id)
            if existing is None:
                merged[result.doc_id] = result
                continue

            existing_score = existing.score or 0.0
            result_score = result.score or 0.0
            merged[result.doc_id] = existing.model_copy(
                update={
                    "score": round(existing_score + result_score, 4),
                    "metadata": {**existing.metadata, "retriever": "hybrid"},
                }
            )

        return sorted(
            merged.values(),
            key=lambda item: item.score or 0.0,
            reverse=True,
        )[:top_k]

    def _fallback_keyword_search(
        self,
        query: str,
        top_k: int,
        metadata_filter: dict[str, Any] | None,
        fallback_from: str,
        error: Exception,
    ) -> list[SourceDocument]:
        results = self._keyword_search(query, top_k, metadata_filter)
        return [
            _with_recovery_metadata(
                result,
                fallback_from=fallback_from,
                fallback_to="keyword",
                error=error,
            )
            for result in results
        ]


def enrich_documents_metadata(documents: list[RawDocument]) -> list[RawDocument]:
    return [enrich_document_metadata(document) for document in documents]


def _create_reranker() -> LlamaIndexReranker | None:
    mode = settings.knowledge_reranker.strip().lower()
    if mode in {"none", "off", "disabled", ""}:
        return None
    if mode == "llamaindex":
        return LlamaIndexReranker(
            vector_weight=settings.knowledge_rerank_vector_weight,
            lexical_weight=settings.knowledge_rerank_lexical_weight,
        )
    raise ValueError(f"Unsupported KNOWLEDGE_RERANKER: {settings.knowledge_reranker}")


def enrich_document_metadata(document: RawDocument) -> RawDocument:
    identity_text = f"{document.title}\n{document.doc_id}".lower()
    full_text = f"{identity_text}\n{document.content}".lower()
    services = _infer_values(text=identity_text, aliases=SERVICE_ALIASES)
    if not services:
        services = _infer_values(text=full_text, aliases=SERVICE_ALIASES)
    incident_types = _infer_values(text=full_text, aliases=INCIDENT_TYPE_ALIASES)
    tags = sorted({*services, *incident_types})
    metadata = {
        **document.metadata,
        "services": services,
        "incident_types": incident_types,
        "tags": tags,
    }
    return RawDocument(
        doc_id=document.doc_id,
        title=document.title,
        content=document.content,
        source=document.source,
        metadata=metadata,
    )


def _infer_values(text: str, aliases: dict[str, set[str]]) -> list[str]:
    values = [
        canonical
        for canonical, candidates in aliases.items()
        if any(candidate in text for candidate in candidates)
    ]
    return sorted(values)


def _canonical_value(value: str, aliases: dict[str, set[str]]) -> str:
    normalized = value.lower()
    for canonical, candidates in aliases.items():
        if normalized == canonical or normalized in candidates:
            return canonical
    return normalized


def _build_query(query: str, keywords: list[str] | None) -> str:
    parts = [query.strip()]
    if keywords:
        parts.extend(keyword.strip() for keyword in keywords if keyword.strip())
    return " ".join(part for part in parts if part)


def _collect_metadata_values(documents: list[RawDocument], key: str) -> list[str]:
    values: set[str] = set()
    for document in documents:
        raw_value = document.metadata.get(key, [])
        if isinstance(raw_value, list):
            values.update(str(item) for item in raw_value)
        elif raw_value:
            values.add(str(raw_value))
    return sorted(values)


def _with_retriever(source: SourceDocument, retriever: str) -> SourceDocument:
    return source.model_copy(update={"metadata": {**source.metadata, "retriever": retriever}})


def _with_recovery_metadata(
    source: SourceDocument,
    fallback_from: str,
    fallback_to: str,
    error: Exception,
) -> SourceDocument:
    return source.model_copy(
        update={
            "metadata": {
                **source.metadata,
                "retriever": fallback_to,
                "recovery": {
                    "used": True,
                    "fallback_from": fallback_from,
                    "fallback_to": fallback_to,
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                },
            }
        }
    )

import hashlib
import json
from typing import Any

from app.config import settings
from app.rag.access_control import KnowledgeAccessContext, can_access_document
from app.rag.embeddings import EmbeddingModel, create_embedding_model
from app.rag.filters import matches_metadata
from app.rag.splitter import DocumentChunk
from app.rag.vector_store import InMemoryVectorStore
from app.schemas import SourceDocument
from app.security_context import current_tenant_id


class MilvusVectorStore:
    """Milvus-backed vector store for production knowledge retrieval."""

    def __init__(
        self,
        embedding_model: EmbeddingModel | None = None,
        uri: str | None = None,
        token: str | None = None,
        db_name: str | None = None,
        collection_name: str | None = None,
        dimensions: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.embedding_model = embedding_model or create_embedding_model()
        self.uri = settings.milvus_uri if uri is None else uri
        self.token = settings.milvus_token if token is None else token
        self.db_name = settings.milvus_db_name if db_name is None else db_name
        self.collection_name = collection_name or settings.milvus_collection_name
        self.dimensions = dimensions or settings.embedding_dimensions
        self.primary_field = settings.milvus_primary_field
        self.vector_field = settings.milvus_vector_field
        self.metric_type = settings.milvus_metric_type.upper()
        self._client = client

        if not self.uri and client is None:
            raise ValueError("MILVUS_URI is required when KNOWLEDGE_VECTOR_STORE=milvus.")
        if self.dimensions <= 0:
            raise ValueError("EMBEDDING_DIMENSIONS must be greater than 0.")

    @classmethod
    def from_chunks(
        cls,
        chunks: list[DocumentChunk],
        embedding_model: EmbeddingModel | None = None,
    ) -> "MilvusVectorStore":
        store = cls(embedding_model=embedding_model)
        store.ensure_collection()
        store.upsert_chunks(chunks)
        return store

    @property
    def client(self):
        if self._client is not None:
            return self._client

        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise RuntimeError(
                "Milvus vector store requires pymilvus. "
                "Install dependencies with: pip install -r requirements.txt"
            ) from exc

        kwargs = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        if self.db_name:
            kwargs["db_name"] = self.db_name

        self._client = MilvusClient(**kwargs)
        return self._client

    def ensure_collection(self) -> None:
        if self.client.has_collection(collection_name=self.collection_name):
            return

        self.client.create_collection(
            collection_name=self.collection_name,
            dimension=self.dimensions,
            primary_field_name=self.primary_field,
            vector_field_name=self.vector_field,
            id_type="string",
            max_length=512,
            metric_type=self.metric_type,
            auto_id=False,
            enable_dynamic_field=True,
        )

    def upsert_chunks(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return

        tenant_id = current_tenant_id()
        records = [
            {
                self.primary_field: _tenant_chunk_id(
                    tenant_id,
                    chunk.chunk_id,
                ),
                self.vector_field: self.embedding_model.embed(f"{chunk.title}\n{chunk.content}"),
                "tenant_id": tenant_id,
                "original_chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "content": chunk.content,
                "source": chunk.source,
                "metadata": json.dumps(chunk.metadata, ensure_ascii=False),
            }
            for chunk in chunks
        ]
        self.client.upsert(collection_name=self.collection_name, data=records)

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        tenant_id = current_tenant_id()
        self.client.delete(
            collection_name=self.collection_name,
            ids=[
                _tenant_chunk_id(tenant_id, chunk_id)
                for chunk_id in chunk_ids
            ],
        )

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

        raw_results = self.client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            anns_field=self.vector_field,
            limit=max(top_k * 10, top_k),
            filter=_tenant_filter(
                access_context.tenant_id
                if access_context
                else settings.default_tenant_id
            ),
            output_fields=[
                self.primary_field,
                "original_chunk_id",
                "doc_id",
                "title",
                "content",
                "source",
                "metadata",
            ],
            search_params={"metric_type": self.metric_type},
        )

        documents: list[SourceDocument] = []
        for hit in _first_result_set(raw_results):
            entity = _entity(hit)
            metadata = _metadata(entity)
            if not can_access_document(metadata, access_context):
                continue
            if metadata_filter and not matches_metadata(metadata, metadata_filter):
                continue

            documents.append(
                SourceDocument(
                    doc_id=str(
                        _field(
                            entity,
                            "original_chunk_id",
                            default=_field(
                                entity,
                                self.primary_field,
                                "id",
                                default=_field(hit, "id", default=""),
                            ),
                        )
                    ),
                    title=str(_field(entity, "title", default="")),
                    content=str(_field(entity, "content", default="")),
                    source=str(_field(entity, "source", default="milvus")),
                    score=_score(hit),
                    metadata={**metadata, "retriever": "milvus"},
                )
            )
            if len(documents) >= top_k:
                break

        return documents


def _first_result_set(raw_results: Any) -> list[Any]:
    if not raw_results:
        return []
    first = raw_results[0]
    return list(first) if isinstance(first, list) else list(raw_results)


def _entity(hit: Any) -> dict[str, Any]:
    entity = _field(hit, "entity", "fields", default={})
    return entity if isinstance(entity, dict) else {}


def _metadata(entity: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = entity.get("metadata", {})
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str) and raw_metadata:
        try:
            parsed = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _field(source: Any, *names: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source[name]
        return default

    for name in names:
        value = getattr(source, name, None)
        if value is not None:
            return value
    return default


def _score(hit: Any) -> float | None:
    raw_score = _field(hit, "score", "distance", default=None)
    if raw_score is None:
        return None
    return round(float(raw_score), 4)


def _tenant_chunk_id(tenant_id: str, chunk_id: str) -> str:
    identity = f"{tenant_id}\0{chunk_id}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _tenant_filter(tenant_id: str) -> str:
    return f"tenant_id == {json.dumps(tenant_id, ensure_ascii=False)}"


__all__ = ["InMemoryVectorStore", "MilvusVectorStore"]

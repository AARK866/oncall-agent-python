import base64
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.config import settings
from app.rag.document_loader import (
    RawDocument,
    load_enterprise_documents,
    load_file_document,
    normalize_extensions,
)
from app.rag.embeddings import create_embedding_model
from app.rag.knowledge_base import enrich_documents_metadata
from app.rag.llamaindex_adapter import create_llamaindex_adapter
from app.rag.milvus_store import MilvusVectorStore
from app.rag.splitter import DocumentChunk, split_documents
from app.rag.vector_store import InMemoryVectorStore
from app.schemas import KnowledgeIngestResponse, KnowledgeIngestSource
from app.tools import GitHubClient


class KnowledgeIngestionPipeline:
    """Load runbooks, split them into chunks, and upsert them into the configured vector store."""

    def __init__(
        self,
        embedding_model: Any | None = None,
        github_client: GitHubClient | None = None,
    ) -> None:
        self.embedding_model = embedding_model or create_embedding_model()
        self.github_client = github_client or GitHubClient()

    async def ingest(
        self,
        source: KnowledgeIngestSource | str | None = None,
        path: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> KnowledgeIngestResponse:
        ingest_source = _ingest_source(source or settings.knowledge_source)
        source_path = path or _default_path(ingest_source)
        raw_documents = await self._load_documents(ingest_source, source_path)
        documents = enrich_documents_metadata(raw_documents)
        chunks = split_documents(
            documents,
            chunk_size=chunk_size or settings.knowledge_ingest_chunk_size,
            chunk_overlap=chunk_overlap if chunk_overlap is not None else settings.knowledge_ingest_chunk_overlap,
        )
        chunks, engine_metadata = self._prepare_for_engine(documents, chunks)
        store_metadata = self._upsert_chunks(chunks)

        return KnowledgeIngestResponse(
            status="ok",
            source=ingest_source,
            path=source_path,
            documents_loaded=len(documents),
            chunks_created=len(chunks),
            vector_store=settings.knowledge_vector_store,
            collection_name=store_metadata.get("collection_name"),
            document_ids=[document.doc_id for document in documents],
            metadata={
                **engine_metadata,
                **store_metadata,
                "embedding_provider": settings.embedding_provider,
                "embedding_model": settings.embedding_model,
                "embedding_dimensions": settings.embedding_dimensions,
                "documents": _document_metadata_summary(documents),
            },
        )

    async def _load_documents(
        self,
        source: KnowledgeIngestSource,
        path: str,
    ) -> list[RawDocument]:
        if source == KnowledgeIngestSource.local:
            return load_enterprise_documents(
                path,
                allowed_extensions=_allowed_extensions(),
                access_scope=settings.knowledge_default_access_scope,
                allowed_roles=_allowed_roles(),
            )

        if source == KnowledgeIngestSource.github:
            return await self._load_github_documents(path)

        raise ValueError(f"Unsupported knowledge source: {source}")

    def _prepare_for_engine(
        self,
        documents: list[RawDocument],
        chunks: list[DocumentChunk],
    ) -> tuple[list[DocumentChunk], dict[str, Any]]:
        engine = settings.knowledge_engine.strip().lower()
        if engine in {"local", "", "default"}:
            return chunks, {"knowledge_engine": "local"}

        if engine == "llamaindex":
            adapter = create_llamaindex_adapter()
            batch = adapter.prepare_ingestion(documents, chunks)
            return (
                batch.chunks,
                {
                    "knowledge_engine": "llamaindex",
                    "llamaindex": {
                        **adapter.describe(),
                        "pipeline": "document-node-store",
                        "documents_prepared": len(batch.documents),
                        "nodes_prepared": len(batch.nodes),
                        "chunks_normalized": len(batch.chunks),
                    },
                },
            )

        raise ValueError(f"Unsupported KNOWLEDGE_ENGINE: {settings.knowledge_engine}")

    async def _load_github_documents(self, path: str) -> list[RawDocument]:
        documents: list[RawDocument] = []
        await self._collect_github_documents(path.strip("/"), root_path=path.strip("/"), documents=documents)
        return documents

    async def _collect_github_documents(
        self,
        path: str,
        root_path: str,
        documents: list[RawDocument],
    ) -> None:
        data = await self.github_client.get_file(path)
        if data.get("type") == "directory":
            for entry in data.get("entries", []):
                entry_path = str(entry.get("path") or "")
                if entry.get("type") in {"dir", "directory"}:
                    await self._collect_github_documents(entry_path, root_path=root_path, documents=documents)
                elif Path(entry_path).suffix.lower() in _allowed_extensions():
                    await self._collect_github_documents(entry_path, root_path=root_path, documents=documents)
            return

        if data.get("type") != "file":
            return
        github_path = str(data.get("path") or path)
        suffix = Path(github_path).suffix.lower()
        if suffix not in _allowed_extensions():
            return

        doc_id = _relative_doc_id(github_path, root_path)
        source_uri = f"github://{self.github_client.repo}/{github_path}"
        content_bytes = _github_content_bytes(data)
        with NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            temporary_file.write(content_bytes)
            temporary_path = Path(temporary_file.name)

        try:
            documents.append(
                load_file_document(
                    temporary_path,
                    doc_id=doc_id,
                    source=source_uri,
                    source_type="github",
                    source_version=str(data.get("sha") or "") or None,
                    updated_at=data.get("updated_at"),
                    access_scope=settings.knowledge_default_access_scope,
                    allowed_roles=_allowed_roles(),
                    extra_metadata={
                        "github_repo": self.github_client.repo,
                        "github_branch": data.get("ref") or self.github_client.branch,
                        "github_sha": data.get("sha"),
                    },
                )
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    def _upsert_chunks(self, chunks: list[DocumentChunk]) -> dict[str, Any]:
        store_mode = settings.knowledge_vector_store.lower().strip()
        if store_mode in {"in_memory", "memory", "local"}:
            InMemoryVectorStore.from_chunks(chunks=chunks, embedding_model=self.embedding_model)
            return {"store": "in_memory", "persisted": False}

        if store_mode == "milvus":
            store = MilvusVectorStore(embedding_model=self.embedding_model)
            store.ensure_collection()
            store.upsert_chunks(chunks)
            return {
                "store": "milvus",
                "persisted": True,
                "collection_name": store.collection_name,
            }

        raise ValueError(f"Unsupported KNOWLEDGE_VECTOR_STORE: {settings.knowledge_vector_store}")


def _ingest_source(value: KnowledgeIngestSource | str) -> KnowledgeIngestSource:
    if isinstance(value, KnowledgeIngestSource):
        return value
    return KnowledgeIngestSource(value.strip().lower())


def _default_path(source: KnowledgeIngestSource) -> str:
    if source == KnowledgeIngestSource.github:
        return settings.knowledge_github_path
    return settings.knowledge_local_path


def _relative_doc_id(path: str, root_path: str) -> str:
    normalized_path = path.strip("/")
    normalized_root = root_path.strip("/")
    if normalized_path == normalized_root:
        return Path(normalized_path).name
    if normalized_root and normalized_path.startswith(f"{normalized_root}/"):
        return normalized_path[len(normalized_root) + 1 :]
    return normalized_path


def _allowed_extensions() -> set[str]:
    return normalize_extensions(settings.knowledge_allowed_extensions.split(","))


def _allowed_roles() -> list[str]:
    return [role.strip() for role in settings.knowledge_default_allowed_roles.split(",") if role.strip()]


def _github_content_bytes(data: dict[str, Any]) -> bytes:
    encoded = data.get("content_base64")
    if isinstance(encoded, str) and encoded:
        return base64.b64decode(encoded)
    return str(data.get("content") or "").encode("utf-8")


def _document_metadata_summary(documents: list[RawDocument]) -> dict[str, Any]:
    formats: dict[str, int] = {}
    access_scopes: set[str] = set()
    roles: set[str] = set()
    for document in documents:
        file_type = str(document.metadata.get("file_type") or "unknown")
        formats[file_type] = formats.get(file_type, 0) + 1
        if document.metadata.get("access_scope"):
            access_scopes.add(str(document.metadata["access_scope"]))
        roles.update(str(role) for role in document.metadata.get("allowed_roles", []))
    return {
        "formats": formats,
        "access_scopes": sorted(access_scopes),
        "allowed_roles": sorted(roles),
    }

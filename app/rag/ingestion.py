from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.document_loader import RawDocument, load_markdown_documents
from app.rag.embeddings import create_embedding_model
from app.rag.knowledge_base import enrich_documents_metadata
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
                **store_metadata,
                "embedding_provider": settings.embedding_provider,
                "embedding_model": settings.embedding_model,
                "embedding_dimensions": settings.embedding_dimensions,
            },
        )

    async def _load_documents(
        self,
        source: KnowledgeIngestSource,
        path: str,
    ) -> list[RawDocument]:
        if source == KnowledgeIngestSource.local:
            return [
                _with_source_metadata(document, source="local")
                for document in load_markdown_documents(path)
            ]

        if source == KnowledgeIngestSource.github:
            return await self._load_github_documents(path)

        raise ValueError(f"Unsupported knowledge source: {source}")

    async def _load_github_documents(self, path: str) -> list[RawDocument]:
        documents: list[RawDocument] = []
        await self._collect_github_markdown(path.strip("/"), root_path=path.strip("/"), documents=documents)
        return documents

    async def _collect_github_markdown(
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
                    await self._collect_github_markdown(entry_path, root_path=root_path, documents=documents)
                elif entry_path.endswith(".md"):
                    await self._collect_github_markdown(entry_path, root_path=root_path, documents=documents)
            return

        if data.get("type") != "file":
            return
        if not str(data.get("path") or path).endswith(".md"):
            return

        content = str(data.get("content") or "")
        doc_id = _relative_doc_id(str(data.get("path") or path), root_path)
        documents.append(
            RawDocument(
                doc_id=doc_id,
                title=_extract_title(content) or Path(doc_id).stem,
                content=content,
                source=f"github://{self.github_client.repo}/{data.get('path')}",
                metadata={
                    "path": doc_id,
                    "github_repo": self.github_client.repo,
                    "github_branch": data.get("ref") or self.github_client.branch,
                    "github_sha": data.get("sha"),
                    "source_type": "github",
                },
            )
        )

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
    if normalized_root and normalized_path.startswith(f"{normalized_root}/"):
        return normalized_path[len(normalized_root) + 1 :]
    return normalized_path


def _with_source_metadata(document: RawDocument, source: str) -> RawDocument:
    return RawDocument(
        doc_id=document.doc_id,
        title=document.title,
        content=document.content,
        source=document.source,
        metadata={**document.metadata, "source_type": source},
    )


def _extract_title(content: str) -> str | None:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return None

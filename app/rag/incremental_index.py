from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.document_loader import RawDocument
from app.rag.splitter import DocumentChunk
from app.schemas import KnowledgeIngestSource
from app.storage import KnowledgeManifestRecord, new_manifest_record


@dataclass(frozen=True)
class IncrementalIndexPlan:
    namespace: str
    index_signature: str
    new_documents: list[RawDocument]
    updated_documents: list[RawDocument]
    unchanged_documents: list[RawDocument]
    deleted_records: list[KnowledgeManifestRecord]

    @property
    def documents_to_index(self) -> list[RawDocument]:
        return [*self.new_documents, *self.updated_documents]


def build_index_namespace(
    source: KnowledgeIngestSource,
    path: str,
    github_repo: str | None = None,
    github_branch: str | None = None,
) -> str:
    if source == KnowledgeIngestSource.github:
        repo = github_repo or "unknown-repository"
        branch = github_branch or "main"
        return f"github:{repo}@{branch}:{path.strip('/')}"
    return f"local:{Path(path).resolve().as_posix()}"


def build_index_signature(chunk_size: int, chunk_overlap: int) -> str:
    return _stable_hash(
        {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "knowledge_engine": settings.knowledge_engine,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "embedding_dimensions": settings.embedding_dimensions,
            "collection_name": settings.milvus_collection_name,
            "vector_field": settings.milvus_vector_field,
            "metric_type": settings.milvus_metric_type,
            "allowed_extensions": sorted(
                value.strip().lower()
                for value in settings.knowledge_allowed_extensions.split(",")
                if value.strip()
            ),
        }
    )


def document_signature(document: RawDocument) -> str:
    governance_keys = (
        "access_scope",
        "allowed_roles",
        "content_sha256",
        "file_type",
        "incident_types",
        "parser",
        "services",
        "source_type",
        "source_uri",
        "source_version",
        "tags",
    )
    return _stable_hash(
        {
            "doc_id": document.doc_id,
            "title": document.title,
            "source": document.source,
            "metadata": {
                key: document.metadata.get(key)
                for key in governance_keys
            },
        }
    )


def plan_incremental_index(
    documents: list[RawDocument],
    existing_records: dict[str, KnowledgeManifestRecord],
    namespace: str,
    index_signature: str,
    full_rebuild: bool = False,
) -> IncrementalIndexPlan:
    new_documents: list[RawDocument] = []
    updated_documents: list[RawDocument] = []
    unchanged_documents: list[RawDocument] = []

    for document in documents:
        existing = existing_records.get(document.doc_id)
        if existing is None:
            new_documents.append(document)
            continue
        if (
            full_rebuild
            or existing.document_signature != document_signature(document)
            or existing.index_signature != index_signature
        ):
            updated_documents.append(document)
        else:
            unchanged_documents.append(document)

    current_doc_ids = {document.doc_id for document in documents}
    deleted_records = [
        record
        for doc_id, record in existing_records.items()
        if doc_id not in current_doc_ids
    ]
    return IncrementalIndexPlan(
        namespace=namespace,
        index_signature=index_signature,
        new_documents=new_documents,
        updated_documents=updated_documents,
        unchanged_documents=unchanged_documents,
        deleted_records=deleted_records,
    )


def build_manifest_records(
    plan: IncrementalIndexPlan,
    chunks: list[DocumentChunk],
) -> list[KnowledgeManifestRecord]:
    chunk_ids_by_doc: dict[str, list[str]] = {}
    for chunk in chunks:
        chunk_ids_by_doc.setdefault(chunk.doc_id, []).append(chunk.chunk_id)

    return [
        new_manifest_record(
            namespace=plan.namespace,
            doc_id=document.doc_id,
            source_uri=str(document.metadata.get("source_uri") or document.source),
            source_version=str(document.metadata.get("source_version") or ""),
            document_signature=document_signature(document),
            index_signature=plan.index_signature,
            chunk_ids=chunk_ids_by_doc.get(document.doc_id, []),
            metadata=document.metadata,
        )
        for document in plan.documents_to_index
    ]


def stale_chunk_ids(
    plan: IncrementalIndexPlan,
    existing_records: dict[str, KnowledgeManifestRecord],
) -> set[str]:
    updated_doc_ids = {document.doc_id for document in plan.updated_documents}
    records = [
        *(existing_records[doc_id] for doc_id in updated_doc_ids),
        *plan.deleted_records,
    ]
    return {
        chunk_id
        for record in records
        for chunk_id in record.chunk_ids
    }


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

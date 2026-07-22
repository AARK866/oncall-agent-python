from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import MetadataMode


SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({".md", ".txt", ".pdf", ".docx"})


@dataclass(frozen=True)
class RawDocument:
    doc_id: str
    title: str
    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_enterprise_documents(
    directory: str | Path,
    allowed_extensions: Iterable[str] | None = None,
    access_scope: str = "internal",
    allowed_roles: Iterable[str] | None = None,
) -> list[RawDocument]:
    base_dir = Path(directory)
    if not base_dir.exists():
        return []

    extensions = normalize_extensions(allowed_extensions)
    documents: list[RawDocument] = []
    for path in sorted(item for item in base_dir.rglob("*") if item.is_file()):
        if path.suffix.lower() not in extensions:
            continue
        documents.append(
            load_file_document(
                path,
                doc_id=path.relative_to(base_dir).as_posix(),
                source_type="local",
                access_scope=access_scope,
                allowed_roles=allowed_roles,
            )
        )
    return documents


def load_file_document(
    path: str | Path,
    doc_id: str | None = None,
    source: str | None = None,
    source_type: str = "local",
    source_version: str | None = None,
    updated_at: str | None = None,
    access_scope: str = "internal",
    allowed_roles: Iterable[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> RawDocument:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported document type: {suffix or '<none>'}")

    content_bytes = file_path.read_bytes()
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()
    document_id = doc_id or file_path.name
    source_uri = source or str(file_path)
    resolved_updated_at = updated_at
    if resolved_updated_at is None and source_type == "local":
        resolved_updated_at = datetime.fromtimestamp(
            file_path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()

    metadata = {
        "path": document_id,
        "file_name": Path(document_id).name,
        "file_type": suffix.removeprefix("."),
        "mime_type": mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        "file_size_bytes": len(content_bytes),
        "source_type": source_type,
        "source_uri": source_uri,
        "source_version": source_version or content_sha256,
        "updated_at": resolved_updated_at,
        "content_sha256": content_sha256,
        "access_scope": access_scope,
        "allowed_roles": normalize_roles(allowed_roles),
        "parser": "llamaindex-simple-directory-reader",
        **(extra_metadata or {}),
    }
    llama_documents = SimpleDirectoryReader(
        input_files=[file_path],
        file_metadata=lambda _: metadata,
        raise_on_error=True,
    ).load_data()
    content = "\n\n".join(
        document.get_content(metadata_mode=MetadataMode.NONE).strip()
        for document in llama_documents
        if document.get_content(metadata_mode=MetadataMode.NONE).strip()
    )
    page_labels = [
        str(document.metadata["page_label"])
        for document in llama_documents
        if document.metadata.get("page_label") is not None
    ]
    if page_labels:
        metadata["page_labels"] = page_labels
    metadata["page_count"] = len(llama_documents) if suffix == ".pdf" else None

    return RawDocument(
        doc_id=document_id,
        title=_extract_title(content) or file_path.stem,
        content=content,
        source=source_uri,
        metadata=metadata,
    )


def load_markdown_documents(directory: str | Path) -> list[RawDocument]:
    return load_enterprise_documents(directory, allowed_extensions={".md"})


def normalize_extensions(values: Iterable[str] | None) -> set[str]:
    extensions = values.split(",") if isinstance(values, str) else values
    extensions = extensions or SUPPORTED_DOCUMENT_EXTENSIONS
    normalized = {
        value.strip().lower() if value.strip().startswith(".") else f".{value.strip().lower()}"
        for value in extensions
        if value.strip()
    }
    unsupported = normalized - SUPPORTED_DOCUMENT_EXTENSIONS
    if unsupported:
        raise ValueError(f"Unsupported knowledge extensions: {', '.join(sorted(unsupported))}")
    return normalized


def normalize_roles(values: Iterable[str] | None) -> list[str]:
    roles = values.split(",") if isinstance(values, str) else values
    return sorted({value.strip() for value in (roles or []) if value.strip()})


def _extract_title(content: str) -> str | None:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return None

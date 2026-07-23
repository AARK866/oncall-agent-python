from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.storage.database import (
    Database,
    DatabaseConnection,
    DatabaseRow,
    configured_database_target,
)


@dataclass(frozen=True)
class KnowledgeManifestRecord:
    namespace: str
    doc_id: str
    source_uri: str
    source_version: str
    document_signature: str
    index_signature: str
    chunk_ids: list[str]
    metadata: dict
    indexed_at: str


class SQLiteKnowledgeManifestStore:
    """Knowledge manifest repository backed by the configured database."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        auto_create_schema: bool = True,
    ) -> None:
        self.db_path = db_path
        self.database = Database(db_path)
        if auto_create_schema:
            self._init_schema()

    @classmethod
    def from_settings(cls) -> "SQLiteKnowledgeManifestStore":
        return cls(
            configured_database_target(settings.knowledge_manifest_db_path),
            auto_create_schema=settings.database_auto_create_schema,
        )

    def list_records(self, namespace: str) -> dict[str, KnowledgeManifestRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_index_manifest WHERE namespace = ?",
                (namespace,),
            ).fetchall()
        return {row["doc_id"]: _record_from_row(row) for row in rows}

    def apply(
        self,
        namespace: str,
        records: list[KnowledgeManifestRecord],
        deleted_doc_ids: list[str],
    ) -> None:
        with self._connect() as connection:
            if deleted_doc_ids:
                placeholders = ",".join("?" for _ in deleted_doc_ids)
                connection.execute(
                    f"DELETE FROM knowledge_index_manifest WHERE namespace = ? AND doc_id IN ({placeholders})",
                    (namespace, *deleted_doc_ids),
                )
            connection.executemany(
                """
                INSERT INTO knowledge_index_manifest (
                    namespace, doc_id, source_uri, source_version,
                    document_signature, index_signature, chunk_ids_json,
                    metadata_json, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, doc_id) DO UPDATE SET
                    source_uri = excluded.source_uri,
                    source_version = excluded.source_version,
                    document_signature = excluded.document_signature,
                    index_signature = excluded.index_signature,
                    chunk_ids_json = excluded.chunk_ids_json,
                    metadata_json = excluded.metadata_json,
                    indexed_at = excluded.indexed_at
                """,
                [
                    (
                        record.namespace,
                        record.doc_id,
                        record.source_uri,
                        record.source_version,
                        record.document_signature,
                        record.index_signature,
                        json.dumps(record.chunk_ids, ensure_ascii=False),
                        json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
                        record.indexed_at,
                    )
                    for record in records
                ],
            )

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_index_manifest (
                    namespace TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    document_signature TEXT NOT NULL,
                    index_signature TEXT NOT NULL,
                    chunk_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, doc_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_knowledge_manifest_namespace
                ON knowledge_index_manifest(namespace)
                """
            )

    def _connect(self) -> DatabaseConnection:
        return self.database.connect()


def new_manifest_record(
    namespace: str,
    doc_id: str,
    source_uri: str,
    source_version: str,
    document_signature: str,
    index_signature: str,
    chunk_ids: list[str],
    metadata: dict,
) -> KnowledgeManifestRecord:
    return KnowledgeManifestRecord(
        namespace=namespace,
        doc_id=doc_id,
        source_uri=source_uri,
        source_version=source_version,
        document_signature=document_signature,
        index_signature=index_signature,
        chunk_ids=chunk_ids,
        metadata=metadata,
        indexed_at=datetime.now(timezone.utc).isoformat(),
    )


def _record_from_row(row: DatabaseRow) -> KnowledgeManifestRecord:
    return KnowledgeManifestRecord(
        namespace=row["namespace"],
        doc_id=row["doc_id"],
        source_uri=row["source_uri"],
        source_version=row["source_version"],
        document_signature=row["document_signature"],
        index_signature=row["index_signature"],
        chunk_ids=list(json.loads(row["chunk_ids_json"])),
        metadata=dict(json.loads(row["metadata_json"])),
        indexed_at=row["indexed_at"],
    )

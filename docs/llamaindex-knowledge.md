# LlamaIndex knowledge adapter

This step introduces a LlamaIndex compatibility layer without replacing the
existing RAG pipeline yet.

## Configuration

```env
KNOWLEDGE_ENGINE=local
```

Supported values:

- `local`: current project-native RAG flow;
- `llamaindex`: prepare LlamaIndex-compatible documents and nodes during
  ingestion while keeping the existing vector store write path.

## What Changed

The adapter converts project-native RAG objects:

```text
RawDocument -> LlamaIndex Document
DocumentChunk -> LlamaIndex TextNode
LlamaIndex node -> SourceDocument
```

If `llama-index-core` is not installed, the adapter returns lightweight snapshot
objects with the same fields this project needs. That keeps tests and local
development stable while the project migrates in small steps.

## Why This Matters

LlamaIndex is built around document and node abstractions. A node is a retrievable
chunk of a source document, and metadata is carried alongside documents and
nodes. This matches the project's enterprise needs:

- stable chunk ids;
- service and incident metadata;
- metadata filtering;
- future rerank and retrieval evaluation;
- future LlamaIndex retriever integration.

## Current Boundary

This step does not replace `KnowledgeBase.search()` yet.

The next LlamaIndex step should move ingestion onto this adapter more deeply:

```text
load documents
  -> enrich metadata
  -> create LlamaIndex documents/nodes
  -> write nodes to current vector store
  -> keep /api/knowledge/ingest compatible
```

Reference: LlamaIndex documents `Document` and `Node` as its core loading
abstractions, where nodes are retrievable chunks with metadata inherited from
their source documents.

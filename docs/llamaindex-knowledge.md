# LlamaIndex knowledge adapter

This step introduces a LlamaIndex compatibility layer without replacing the
existing RAG pipeline yet.

## Configuration

```env
KNOWLEDGE_ENGINE=local
```

Supported values:

- `local`: current project-native RAG flow;
- `llamaindex`: pass ingestion data through LlamaIndex-compatible documents
  and nodes before writing normalized chunks to the configured vector store.

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

## Ingestion Flow

```text
RawDocument
  -> metadata enrichment
  -> project chunking
  -> LlamaIndex Document + TextNode
  -> normalized DocumentChunk
  -> embedding
  -> in-memory store or Milvus
```

The node metadata carries stable `chunk_id`, `doc_id`, `title`, `source`, and
`knowledge_engine` fields. As a result, the data that reaches the vector store
can be traced back to both the source document and the ingestion engine.

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

The write path now goes through the LlamaIndex adapter when
`KNOWLEDGE_ENGINE=llamaindex`. Retrieval still uses the project's current
keyword, vector, and hybrid implementations.

The next LlamaIndex step should introduce a retriever adapter:

```text
query
  -> current retrieval contract
  -> LlamaIndex retriever
  -> SourceDocument
  -> existing Agent context
```

Reference: LlamaIndex documents `Document` and `Node` as its core loading
abstractions, where nodes are retrievable chunks with metadata inherited from
their source documents.

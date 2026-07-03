from app.rag.vector_store import InMemoryVectorStore


class MilvusVectorStore:
    """Placeholder adapter for a future Milvus-backed vector store."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "MilvusVectorStore is reserved for the production vector database phase. "
            "Use InMemoryVectorStore while learning the retrieval flow."
        )


__all__ = ["InMemoryVectorStore", "MilvusVectorStore"]

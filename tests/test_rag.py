from app.rag import HashEmbeddingModel, InMemoryVectorStore, KnowledgeBase, LocalKnowledgeBase


def test_local_knowledge_base_searches_runbook() -> None:
    kb = LocalKnowledgeBase.from_directory("app/data/runbooks")

    results = kb.search("payment 服务 5xx 升高怎么办", top_k=2)

    assert results
    assert results[0].title == "Payment 服务 5xx 告警处理手册"
    assert results[0].score is not None
    assert "5xx" in results[0].content


def test_knowledge_base_filters_by_service_and_incident_type() -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks")

    results = kb.search("5xx error rate", service="payment-api", incident_type="5xx", top_k=2)

    assert results
    assert results[0].metadata["services"] == ["payment-api"]
    assert results[0].metadata["incident_types"] == ["5xx", "database", "deployment", "timeout"]
    assert kb.stats()["document_count"] >= 2


def test_knowledge_base_can_search_another_runbook() -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks")

    results = kb.search("timeout latency", service="order-api", incident_type="timeout", top_k=1)

    assert results
    assert results[0].title == "Order API Timeout Runbook"


def test_vector_knowledge_base_searches_runbook() -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks", retriever_mode="vector")

    results = kb.search("database connection pool exhausted", service="payment-api", top_k=1)

    assert results
    assert results[0].metadata["retriever"] == "vector"
    assert results[0].metadata["services"] == ["payment-api"]
    assert kb.stats()["retriever_mode"] == "vector"


def test_hybrid_knowledge_base_merges_keyword_and_vector_results() -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks", retriever_mode="hybrid")

    results = kb.search("payment 5xx database", service="payment-api", top_k=2)

    assert results
    assert results[0].metadata["retriever"] in {"hybrid", "keyword", "vector"}


def test_hash_embedding_model_is_deterministic() -> None:
    model = HashEmbeddingModel(dimensions=16)

    assert model.embed("payment 5xx") == model.embed("payment 5xx")
    assert len(model.embed("payment 5xx")) == 16


def test_in_memory_vector_store_can_filter_metadata() -> None:
    kb = KnowledgeBase.from_directory("app/data/runbooks")
    store = InMemoryVectorStore.from_chunks(kb.chunks)

    results = store.search("timeout latency", metadata_filter={"services": "order-api"}, top_k=1)

    assert results
    assert results[0].metadata["services"] == ["order-api"]

from app.rag import LlamaIndexDocumentSnapshot, LlamaIndexNodeSnapshot, create_llamaindex_adapter
from app.rag.document_loader import RawDocument
from app.rag.splitter import DocumentChunk


def test_llamaindex_adapter_preserves_raw_document_metadata() -> None:
    adapter = create_llamaindex_adapter()
    document = RawDocument(
        doc_id="payment.md",
        title="Payment Runbook",
        content="Payment 5xx recovery.",
        source="app/data/runbooks/payment.md",
        metadata={"services": ["payment-api"]},
    )

    llama_document = adapter.document_from_raw(document)
    metadata = getattr(llama_document, "metadata", None)

    assert metadata["doc_id"] == "payment.md"
    assert metadata["title"] == "Payment Runbook"
    assert metadata["source"] == "app/data/runbooks/payment.md"
    assert metadata["services"] == ["payment-api"]

    if isinstance(llama_document, LlamaIndexDocumentSnapshot):
        assert llama_document.text == document.content


def test_llamaindex_adapter_converts_chunk_to_source_document() -> None:
    adapter = create_llamaindex_adapter()
    chunk = DocumentChunk(
        chunk_id="payment.md#chunk-0",
        doc_id="payment.md",
        title="Payment Runbook",
        content="Check payment-api 5xx and database pool.",
        source="app/data/runbooks/payment.md",
        metadata={
            "services": ["payment-api"],
            "incident_types": ["5xx", "database"],
            "chunk_index": 0,
        },
    )

    node = adapter.node_from_chunk(chunk)
    source = adapter.source_from_node(node, score=0.9)

    assert source.doc_id == "payment.md#chunk-0"
    assert source.title == "Payment Runbook"
    assert source.content == chunk.content
    assert source.source == chunk.source
    assert source.score == 0.9
    assert source.metadata["doc_id"] == "payment.md"
    assert source.metadata["services"] == ["payment-api"]

    if isinstance(node, LlamaIndexNodeSnapshot):
        assert node.text == chunk.content

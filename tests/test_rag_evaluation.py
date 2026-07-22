import json

import pytest

from app.rag.evaluation import (
    RagRetrievalEvaluator,
    RetrievalEvaluationCase,
    load_evaluation_cases,
)
from app.schemas import SourceDocument


def test_rag_evaluator_calculates_document_level_hit_rate_and_mrr() -> None:
    evaluator = RagRetrievalEvaluator(FakeKnowledgeBase())
    cases = [
        RetrievalEvaluationCase(
            case_id="found-at-rank-two",
            query="find payment",
            expected_doc_ids=["payment.md"],
            top_k=2,
        ),
        RetrievalEvaluationCase(
            case_id="missing",
            query="missing expected document",
            expected_doc_ids=["missing.md"],
            top_k=2,
        ),
    ]

    report = evaluator.evaluate(cases)

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.failed_cases == 1
    assert report.hit_rate == 0.5
    assert report.mrr == 0.25
    assert report.results[0].retrieved_doc_ids == ["other.md", "payment.md"]
    assert report.results[0].mrr == 0.5


def test_load_evaluation_cases_reports_invalid_line(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "valid",
                "query": "payment 5xx",
                "expected_doc_ids": ["payment.md"],
            }
        )
        + "\nnot-json\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=":2"):
        load_evaluation_cases(dataset)


def test_rag_evaluator_normalizes_chunk_id_without_document_metadata() -> None:
    evaluator = RagRetrievalEvaluator(ChunkOnlyKnowledgeBase())
    report = evaluator.evaluate(
        [
            RetrievalEvaluationCase(
                case_id="chunk-only",
                query="payment",
                expected_doc_ids=["payment.md"],
            )
        ]
    )

    assert report.hit_rate == 1.0
    assert report.results[0].retrieved_doc_ids == ["payment.md"]


class FakeKnowledgeBase:
    def search(self, query: str, **kwargs) -> list[SourceDocument]:
        return [
            _source("other.md#chunk-0", "other.md", 0.9),
            _source("payment.md#chunk-2", "payment.md", 0.8),
            _source("payment.md#chunk-3", "payment.md", 0.7),
        ]


class ChunkOnlyKnowledgeBase:
    def search(self, query: str, **kwargs) -> list[SourceDocument]:
        return [
            SourceDocument(
                doc_id="payment.md#chunk-4",
                title="Payment",
                content="Payment recovery",
                score=0.8,
            )
        ]


def _source(chunk_id: str, doc_id: str, score: float) -> SourceDocument:
    return SourceDocument(
        doc_id=chunk_id,
        title=doc_id,
        content=doc_id,
        score=score,
        metadata={"doc_id": doc_id},
    )

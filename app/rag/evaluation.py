from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from llama_index.core.evaluation import HitRate, MRR
from pydantic import BaseModel, Field

from app.schemas import SourceDocument


class RetrievalEvaluationCase(BaseModel):
    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    expected_doc_ids: list[str] = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    service: str | None = None
    incident_type: str | None = None
    keywords: list[str] = Field(default_factory=list)


class RetrievalEvaluationResult(BaseModel):
    case_id: str
    query: str
    expected_doc_ids: list[str]
    retrieved_doc_ids: list[str]
    retrieved_chunk_ids: list[str]
    scores: list[float | None]
    hit_rate: float
    mrr: float
    passed: bool


class RetrievalEvaluationReport(BaseModel):
    metric_provider: str = "llamaindex"
    total_cases: int
    passed_cases: int
    failed_cases: int
    hit_rate: float
    mrr: float
    results: list[RetrievalEvaluationResult] = Field(default_factory=list)


class KnowledgeSearcher(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 3,
        service: str | None = None,
        incident_type: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[SourceDocument]: ...


class RagRetrievalEvaluator:
    """Evaluate document retrieval with LlamaIndex Hit Rate and MRR metrics."""

    def __init__(self, knowledge_base: KnowledgeSearcher) -> None:
        self.knowledge_base = knowledge_base
        self._hit_rate = HitRate()
        self._mrr = MRR()

    def evaluate(
        self,
        cases: list[RetrievalEvaluationCase],
        top_k: int | None = None,
    ) -> RetrievalEvaluationReport:
        results = [self.evaluate_case(case, top_k=top_k) for case in cases]
        total = len(results)
        passed = sum(1 for result in results if result.passed)
        return RetrievalEvaluationReport(
            total_cases=total,
            passed_cases=passed,
            failed_cases=total - passed,
            hit_rate=_average(result.hit_rate for result in results),
            mrr=_average(result.mrr for result in results),
            results=results,
        )

    def evaluate_case(
        self,
        case: RetrievalEvaluationCase,
        top_k: int | None = None,
    ) -> RetrievalEvaluationResult:
        sources = self.knowledge_base.search(
            query=case.query,
            top_k=top_k or case.top_k,
            service=case.service,
            incident_type=case.incident_type,
            keywords=case.keywords,
        )
        retrieved_doc_ids = _document_ids(sources)
        hit_rate = _metric_score(
            self._hit_rate.compute(
                query=case.query,
                expected_ids=case.expected_doc_ids,
                retrieved_ids=retrieved_doc_ids,
            )
        )
        mrr = _metric_score(
            self._mrr.compute(
                query=case.query,
                expected_ids=case.expected_doc_ids,
                retrieved_ids=retrieved_doc_ids,
            )
        )
        return RetrievalEvaluationResult(
            case_id=case.case_id,
            query=case.query,
            expected_doc_ids=case.expected_doc_ids,
            retrieved_doc_ids=retrieved_doc_ids,
            retrieved_chunk_ids=[source.doc_id for source in sources],
            scores=[source.score for source in sources],
            hit_rate=hit_rate,
            mrr=mrr,
            passed=hit_rate > 0,
        )


def load_evaluation_cases(path: str | Path) -> list[RetrievalEvaluationCase]:
    dataset_path = Path(path)
    cases: list[RetrievalEvaluationCase] = []
    for line_number, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
            cases.append(RetrievalEvaluationCase.model_validate(payload))
        except Exception as exc:
            raise ValueError(f"Invalid evaluation case at {dataset_path}:{line_number}: {exc}") from exc

    if not cases:
        raise ValueError(f"Evaluation dataset is empty: {dataset_path}")
    return cases


def _document_ids(sources: list[SourceDocument]) -> list[str]:
    document_ids: list[str] = []
    for source in sources:
        doc_id = str(source.metadata.get("doc_id") or source.doc_id).split("#chunk-", 1)[0]
        if doc_id not in document_ids:
            document_ids.append(doc_id)
    return document_ids


def _metric_score(result: Any) -> float:
    return round(float(result.score or 0.0), 6)


def _average(values: Any) -> float:
    collected = list(values)
    return round(sum(collected) / len(collected), 6) if collected else 0.0

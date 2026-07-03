from app.rag import LocalKnowledgeBase


def test_local_knowledge_base_searches_runbook() -> None:
    kb = LocalKnowledgeBase.from_directory("app/data/runbooks")

    results = kb.search("payment 服务 5xx 升高怎么办", top_k=2)

    assert results
    assert results[0].title == "Payment 服务 5xx 告警处理手册"
    assert results[0].score is not None
    assert "5xx" in results[0].content

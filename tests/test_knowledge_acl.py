from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.rag import KnowledgeAccessContext, KnowledgeBase, can_access_document
from app.rag.document_loader import RawDocument
from app.rag.splitter import DocumentChunk


client = TestClient(app)


def test_acl_scope_and_role_rules() -> None:
    anonymous = KnowledgeAccessContext.from_roles(
        subject="anonymous",
        roles=[],
        authenticated=False,
    )
    oncall = _principal("oncall")

    assert can_access_document({"access_scope": "public"}, anonymous) is True
    assert can_access_document(
        {"access_scope": "restricted", "allowed_roles": ["sre"]},
        oncall,
    ) is False
    assert can_access_document(
        {"access_scope": "restricted", "allowed_roles": ["oncall"]},
        oncall,
    ) is True
    assert can_access_document({"access_scope": "unknown"}, oncall) is False


def test_document_tenant_is_enforced_even_when_acl_is_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "knowledge_acl_enabled", False)
    tenant_a = KnowledgeAccessContext.from_roles(
        subject="tenant-a-user",
        tenant_id="tenant-a",
        roles=["sre"],
    )
    tenant_b = KnowledgeAccessContext.from_roles(
        subject="tenant-b-user",
        tenant_id="tenant-b",
        roles=["sre"],
    )
    metadata = {"tenant_id": "tenant-a", "access_scope": "public"}

    assert can_access_document(metadata, tenant_a) is True
    assert can_access_document(metadata, tenant_b) is False


def test_knowledge_base_filters_before_returning_top_k() -> None:
    knowledge_base = _acl_knowledge_base()

    viewer_results = knowledge_base.search(
        "payment recovery",
        top_k=5,
        access_context=_principal("viewer"),
    )
    sre_results = knowledge_base.search(
        "payment recovery",
        top_k=5,
        access_context=_principal("sre"),
    )

    assert [result.doc_id for result in viewer_results] == ["public.md#chunk-0"]
    assert {result.doc_id for result in sre_results} == {
        "public.md#chunk-0",
        "restricted.md#chunk-0",
    }
    assert knowledge_base.get_document("restricted.md", _principal("viewer")) is None
    assert knowledge_base.stats(_principal("viewer"))["document_count"] == 1


def test_knowledge_api_requires_token_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "api_token", "knowledge-token")

    response = client.post("/api/knowledge/search", json={"query": "payment runbook"})

    assert response.status_code == 401


def test_knowledge_api_filters_documents_by_token_roles(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "knowledge-token")
    monkeypatch.setattr(settings, "api_token_roles", "viewer")

    denied = client.post(
        "/api/knowledge/search",
        headers={"X-API-Key": "knowledge-token"},
        json={"query": "payment 5xx", "top_k": 2},
    )
    monkeypatch.setattr(settings, "api_token_roles", "oncall")
    allowed = client.post(
        "/api/knowledge/search",
        headers={"X-API-Key": "knowledge-token"},
        json={"query": "payment 5xx", "top_k": 2},
    )

    assert denied.status_code == 200
    assert denied.json()["results"] == []
    assert allowed.status_code == 200
    assert allowed.json()["results"]


def test_chat_does_not_send_unauthorized_runbooks_to_agent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "knowledge-token")
    monkeypatch.setattr(settings, "api_token_roles", "viewer")

    response = client.post(
        "/api/chat",
        headers={"X-API-Key": "knowledge-token"},
        json={
            "message": "payment runbook",
            "session_id": "acl-chat",
            "mode": "knowledge",
        },
    )

    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert response.json()["metadata"]["access_control"]["roles"] == ["viewer"]


def _principal(role: str) -> KnowledgeAccessContext:
    return KnowledgeAccessContext.from_roles(subject=f"test-{role}", roles=[role])


def _acl_knowledge_base() -> KnowledgeBase:
    public_metadata = {"access_scope": "public", "allowed_roles": []}
    restricted_metadata = {"access_scope": "restricted", "allowed_roles": ["sre"]}
    documents = [
        RawDocument(
            doc_id="public.md",
            title="Public Runbook",
            content="Public payment recovery.",
            source="public.md",
            metadata=public_metadata,
        ),
        RawDocument(
            doc_id="restricted.md",
            title="Restricted Runbook",
            content="Restricted payment recovery.",
            source="restricted.md",
            metadata=restricted_metadata,
        ),
    ]
    chunks = [
        DocumentChunk(
            chunk_id="public.md#chunk-0",
            doc_id="public.md",
            title="Public Runbook",
            content="Public payment recovery.",
            source="public.md",
            metadata=public_metadata,
        ),
        DocumentChunk(
            chunk_id="restricted.md#chunk-0",
            doc_id="restricted.md",
            title="Restricted Runbook",
            content="Restricted payment recovery.",
            source="restricted.md",
            metadata=restricted_metadata,
        ),
    ]
    return KnowledgeBase(documents=documents, chunks=chunks, retriever_mode="keyword")

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.config import settings


@dataclass(frozen=True)
class KnowledgeAccessContext:
    subject: str
    roles: frozenset[str]
    tenant_id: str = "default"
    authenticated: bool = True
    source: str = "system"

    @classmethod
    def from_roles(
        cls,
        subject: str,
        roles: Iterable[str],
        tenant_id: str | None = None,
        authenticated: bool = True,
        source: str = "system",
    ) -> "KnowledgeAccessContext":
        return cls(
            subject=subject,
            roles=frozenset(_normalized_values(roles)),
            tenant_id=tenant_id or settings.default_tenant_id,
            authenticated=authenticated,
            source=source,
        )


def system_access_context() -> KnowledgeAccessContext:
    return KnowledgeAccessContext.from_roles(
        subject=settings.knowledge_system_subject,
        roles=settings.knowledge_system_roles.split(","),
        authenticated=True,
        source="system",
    )


def can_access_document(
    metadata: dict[str, Any],
    context: KnowledgeAccessContext | None,
) -> bool:
    principal = context or system_access_context()
    document_tenant = str(
        metadata.get("tenant_id") or settings.default_tenant_id
    ).strip()
    if document_tenant != principal.tenant_id:
        return False
    if not settings.knowledge_acl_enabled:
        return True

    scope = str(metadata.get("access_scope") or "internal").strip().lower()
    if scope == "public":
        return True
    if scope not in {"internal", "restricted"} or not principal.authenticated:
        return False

    raw_allowed_roles = metadata.get("allowed_roles")
    if isinstance(raw_allowed_roles, str):
        role_values = raw_allowed_roles.split(",")
    elif isinstance(raw_allowed_roles, (list, tuple, set)):
        role_values = raw_allowed_roles
    else:
        role_values = settings.knowledge_default_allowed_roles.split(",")
    allowed_roles = _normalized_values(role_values)
    if not allowed_roles:
        return scope == "internal"
    return not principal.roles.isdisjoint(allowed_roles)


def _normalized_values(values: Iterable[Any]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}

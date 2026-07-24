from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from app.config import settings

if TYPE_CHECKING:
    from app.rag.access_control import KnowledgeAccessContext


@dataclass(frozen=True)
class AuthPrincipal:
    subject: str
    tenant_id: str
    roles: frozenset[str]
    permissions: frozenset[str]
    authenticated: bool = True
    source: str = "system"

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions

    def to_knowledge_context(self) -> KnowledgeAccessContext:
        from app.rag.access_control import KnowledgeAccessContext

        return KnowledgeAccessContext(
            subject=self.subject,
            tenant_id=self.tenant_id,
            roles=self.roles,
            authenticated=self.authenticated,
            source=self.source,
        )


_current_principal: ContextVar[AuthPrincipal | None] = ContextVar(
    "oncall_current_principal",
    default=None,
)
_system_database_access: ContextVar[bool] = ContextVar(
    "oncall_system_database_access",
    default=False,
)


def current_principal() -> AuthPrincipal | None:
    return _current_principal.get()


def current_tenant_id() -> str:
    principal = current_principal()
    return principal.tenant_id if principal else settings.default_tenant_id


def has_system_database_access() -> bool:
    return _system_database_access.get()


@contextmanager
def principal_scope(principal: AuthPrincipal | None) -> Iterator[None]:
    token = _current_principal.set(principal)
    try:
        yield
    finally:
        _current_principal.reset(token)


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    existing = current_principal()
    principal = AuthPrincipal(
        subject=existing.subject if existing else "background-worker",
        tenant_id=tenant_id,
        roles=existing.roles if existing else frozenset({"system"}),
        permissions=existing.permissions if existing else frozenset({"*"}),
        authenticated=True,
        source=existing.source if existing else "worker",
    )
    with principal_scope(principal):
        yield


@contextmanager
def system_database_scope() -> Iterator[None]:
    token = _system_database_access.set(True)
    try:
        yield
    finally:
        _system_database_access.reset(token)

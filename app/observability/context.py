from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_trace_id: ContextVar[str | None] = ContextVar(
    "oncall_trace_id",
    default=None,
)


def current_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_scope(trace_id: str) -> Iterator[None]:
    token = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(token)

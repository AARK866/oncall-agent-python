from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from app.config import settings


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0
    retryable_status_codes: set[int] = field(default_factory=lambda: {408, 409, 425, 429, 500, 502, 503, 504})

    @classmethod
    def from_settings(cls) -> "RetryPolicy":
        return cls(
            max_attempts=max(1, settings.tool_retry_max_attempts),
            base_delay_seconds=max(0.0, settings.tool_retry_base_delay_seconds),
            max_delay_seconds=max(0.0, settings.tool_retry_max_delay_seconds),
        )


@dataclass(frozen=True)
class RetryOutcome:
    value: T
    attempts: int
    errors: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "retried": self.attempts > 1,
            "errors": self.errors,
        }


class RetryError(Exception):
    def __init__(self, last_error: Exception, attempts: int, errors: list[str]) -> None:
        super().__init__(str(last_error))
        self.last_error = last_error
        self.attempts = attempts
        self.errors = errors

    def metadata(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "retried": self.attempts > 1,
            "errors": self.errors,
            "final_error_type": type(self.last_error).__name__,
        }


async def run_with_retry(
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
) -> RetryOutcome[T]:
    errors: list[str] = []
    attempts = max(1, policy.max_attempts)

    for attempt in range(1, attempts + 1):
        try:
            value = await operation()
            return RetryOutcome(value=value, attempts=attempt, errors=errors)
        except Exception as exc:
            errors.append(_error_summary(exc))
            if attempt >= attempts or not _is_retryable_exception(exc, policy):
                raise RetryError(last_error=exc, attempts=attempt, errors=errors) from exc
            await asyncio.sleep(_retry_delay(attempt, policy))

    raise RuntimeError("unreachable retry state")


def _retry_delay(attempt: int, policy: RetryPolicy) -> float:
    delay = policy.base_delay_seconds * (2 ** max(0, attempt - 1))
    return min(delay, policy.max_delay_seconds)


def _is_retryable_exception(exc: Exception, policy: RetryPolicy) -> bool:
    status_code = _status_code(exc)
    if status_code is not None:
        return status_code in policy.retryable_status_codes

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    name = type(exc).__name__.lower()
    return any(
        marker in name
        for marker in (
            "timeout",
            "connect",
            "network",
            "temporary",
            "remoteprotocol",
            "readerror",
            "writeerror",
        )
    )


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return int(value) if isinstance(value, int) else None


def _error_summary(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"

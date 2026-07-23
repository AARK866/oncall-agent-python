from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import uuid4

from redis import Redis

from app.config import settings

_COMPARE_AND_DELETE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class DispatchReservation:
    key: str
    token: str


class RedisExecutionLease:
    def __init__(
        self,
        client: Redis,
        key: str,
        ttl_seconds: int,
    ) -> None:
        self.client = client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.token = uuid4().hex
        self.acquired = False

    def __enter__(self) -> "RedisExecutionLease":
        self.acquired = bool(
            self.client.set(
                self.key,
                self.token,
                nx=True,
                ex=self.ttl_seconds,
            )
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.acquired:
            self.client.eval(
                _COMPARE_AND_DELETE,
                1,
                self.key,
                self.token,
            )
            self.acquired = False


class RedisCoordinator:
    """Redis-backed short-lived deduplication and execution leases."""

    def __init__(self, client: Redis | None = None) -> None:
        self.client = client or redis_client_from_settings()

    def ping(self) -> bool:
        return bool(self.client.ping())

    def reserve_dispatch(
        self,
        task_kind: str,
        business_task_id: str,
    ) -> DispatchReservation | None:
        key = self._key("dispatch", task_kind, business_task_id)
        token = uuid4().hex
        reserved = self.client.set(
            key,
            token,
            nx=True,
            ex=settings.task_dispatch_dedupe_ttl_seconds,
        )
        if not reserved:
            return None
        return DispatchReservation(key=key, token=token)

    def release_dispatch(self, reservation: DispatchReservation) -> None:
        self.client.eval(
            _COMPARE_AND_DELETE,
            1,
            reservation.key,
            reservation.token,
        )

    def execution_lease(
        self,
        task_kind: str,
        business_task_id: str,
    ) -> RedisExecutionLease:
        return RedisExecutionLease(
            client=self.client,
            key=self._key("lock", task_kind, business_task_id),
            ttl_seconds=settings.task_execution_lock_ttl_seconds,
        )

    def queue_depths(self) -> dict[str, int]:
        return {
            queue: int(self.client.llen(queue))
            for queue in ("diagnosis", "knowledge", "maintenance")
        }

    @staticmethod
    def _key(namespace: str, task_kind: str, business_task_id: str) -> str:
        return (
            f"{settings.redis_key_prefix}:{namespace}:"
            f"{task_kind}:{business_task_id}"
        )


def redis_client_from_settings() -> Redis:
    return _redis_client(settings.redis_url)


@lru_cache(maxsize=16)
def _redis_client(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        health_check_interval=30,
    )

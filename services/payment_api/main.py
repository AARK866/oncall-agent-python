import asyncio
import json
import os
import random
import secrets
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field


SERVICE_NAME = "payment-api"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PaymentApiSettings:
    environment: str = "local"
    database_path: Path = Path("app/data/payment-api.db")
    log_file_path: Path | None = Path("app/data/payment-api.log")
    fault_injection_enabled: bool = False
    fault_admin_token: str | None = None

    def __post_init__(self) -> None:
        environment = self.environment.strip().lower()
        object.__setattr__(self, "environment", environment)
        if environment == "production" and self.fault_injection_enabled:
            raise ValueError(
                "Fault injection cannot be enabled in production."
            )
        if self.fault_injection_enabled and (
            not self.fault_admin_token
            or len(self.fault_admin_token) < 16
        ):
            raise ValueError(
                "PAYMENT_API_FAULT_ADMIN_TOKEN must contain at least "
                "16 characters when fault injection is enabled."
            )

    @classmethod
    def from_env(cls) -> "PaymentApiSettings":
        log_path = os.getenv(
            "PAYMENT_API_LOG_FILE",
            "app/data/payment-api.log",
        ).strip()
        token = os.getenv("PAYMENT_API_FAULT_ADMIN_TOKEN", "").strip()
        return cls(
            environment=os.getenv("PAYMENT_API_ENV", "local"),
            database_path=Path(
                os.getenv(
                    "PAYMENT_API_DATABASE_PATH",
                    "app/data/payment-api.db",
                )
            ),
            log_file_path=Path(log_path) if log_path else None,
            fault_injection_enabled=_env_bool(
                "PAYMENT_API_ENABLE_FAULT_INJECTION"
            ),
            fault_admin_token=token or None,
        )


class JsonEventLogger:
    def __init__(self, file_path: Path | None) -> None:
        self.file_path = file_path
        self._lock = RLock()
        if self.file_path is not None:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, level: str, message: str, **fields: Any) -> None:
        line = json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level.upper(),
                "logger": SERVICE_NAME,
                "service": SERVICE_NAME,
                "message": message,
                **fields,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        print(line, flush=True)
        if self.file_path is None:
            return
        try:
            with self._lock, self.file_path.open(
                "a",
                encoding="utf-8",
            ) as log_file:
                log_file.write(f"{line}\n")
        except OSError as exc:
            print(
                f"payment-api file logging failed: {exc}",
                file=sys.stderr,
                flush=True,
            )


class PayRequest(BaseModel):
    order_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    user_id: str = Field(min_length=1, max_length=64)
    amount: int = Field(
        gt=0,
        le=100_000_000,
        description="Amount in the currency's smallest unit.",
    )
    currency: Literal["CNY", "JPY", "USD"] = "CNY"
    channel: Literal["card", "wallet", "bank"] = "card"


class RefundRequest(BaseModel):
    payment_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(
        default="customer_request",
        min_length=1,
        max_length=200,
    )


class Fault5xxRequest(BaseModel):
    enabled: bool
    ratio: float = Field(ge=0, le=1)


class FaultLatencyRequest(BaseModel):
    enabled: bool
    delay_ms: int = Field(ge=0, le=30_000)


class FaultChannelRequest(BaseModel):
    enabled: bool
    channel: Literal["card", "wallet", "bank"]
    ratio: float = Field(ge=0, le=1)


class PaymentDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    order_id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    refund_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_payments_created_at
                ON payments(created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    def get(self, payment_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id=?",
                (payment_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_or_get(
        self,
        payload: PayRequest,
    ) -> tuple[dict[str, Any], bool]:
        now = datetime.now(timezone.utc).isoformat()
        payment_id = f"pay_{uuid.uuid4().hex[:16]}"
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM payments WHERE order_id=?",
                (payload.order_id,),
            ).fetchone()
            if existing:
                return dict(existing), True
            connection.execute(
                """
                INSERT INTO payments (
                    payment_id, order_id, user_id, amount, currency,
                    channel, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'paid', ?, ?)
                """,
                (
                    payment_id,
                    payload.order_id,
                    payload.user_id,
                    payload.amount,
                    payload.currency,
                    payload.channel,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id=?",
                (payment_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Payment creation did not persist.")
            return dict(row), False

    def refund(
        self,
        payment_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE payments
                SET status='refunded', refund_reason=?, updated_at=?
                WHERE payment_id=? AND status='paid'
                """,
                (reason, now, payment_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id=?",
                (payment_id,),
            ).fetchone()
            return dict(row) if row else None


class FaultController:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[str, Any] = {
            "error_5xx_enabled": False,
            "error_5xx_ratio": 0.0,
            "latency_enabled": False,
            "delay_ms": 0,
            "channel_failure_enabled": False,
            "channel": None,
            "channel_ratio": 0.0,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def set_5xx(self, payload: Fault5xxRequest) -> dict[str, Any]:
        with self._lock:
            self._state["error_5xx_enabled"] = payload.enabled
            self._state["error_5xx_ratio"] = (
                payload.ratio if payload.enabled else 0.0
            )
            return dict(self._state)

    def set_latency(
        self,
        payload: FaultLatencyRequest,
    ) -> dict[str, Any]:
        with self._lock:
            self._state["latency_enabled"] = payload.enabled
            self._state["delay_ms"] = (
                payload.delay_ms if payload.enabled else 0
            )
            return dict(self._state)

    def set_channel(
        self,
        payload: FaultChannelRequest,
    ) -> dict[str, Any]:
        with self._lock:
            self._state["channel_failure_enabled"] = payload.enabled
            self._state["channel"] = (
                payload.channel if payload.enabled else None
            )
            self._state["channel_ratio"] = (
                payload.ratio if payload.enabled else 0.0
            )
            return dict(self._state)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._state.update(
                {
                    "error_5xx_enabled": False,
                    "error_5xx_ratio": 0.0,
                    "latency_enabled": False,
                    "delay_ms": 0,
                    "channel_failure_enabled": False,
                    "channel": None,
                    "channel_ratio": 0.0,
                }
            )
            return dict(self._state)


class PaymentMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.http_requests = Counter(
            "http_requests_total",
            "Total HTTP requests.",
            ["service", "method", "path", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration.",
            ["service", "method", "path"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10),
            registry=self.registry,
        )
        self.payment_requests = Counter(
            "payment_requests_total",
            "Payment requests by channel and result.",
            ["channel", "result"],
            registry=self.registry,
        )
        self.payment_failures = Counter(
            "payment_failures_total",
            "Payment failures by reason.",
            ["reason"],
            registry=self.registry,
        )
        self.fault_5xx = Gauge(
            "payment_fault_5xx_enabled",
            "Whether the controlled 5xx fault is enabled.",
            registry=self.registry,
        )
        self.fault_latency = Gauge(
            "payment_fault_latency_enabled",
            "Whether the controlled latency fault is enabled.",
            registry=self.registry,
        )
        self.fault_channel = Gauge(
            "payment_fault_channel_enabled",
            "Whether the controlled channel fault is enabled.",
            registry=self.registry,
        )

    def sync_faults(self, state: dict[str, Any]) -> None:
        self.fault_5xx.set(1 if state["error_5xx_enabled"] else 0)
        self.fault_latency.set(1 if state["latency_enabled"] else 0)
        self.fault_channel.set(
            1 if state["channel_failure_enabled"] else 0
        )


def create_payment_app(
    config: PaymentApiSettings | None = None,
) -> FastAPI:
    service_config = config or PaymentApiSettings.from_env()
    database = PaymentDatabase(service_config.database_path)
    event_logger = JsonEventLogger(service_config.log_file_path)
    fault_controller = FaultController()
    metrics = PaymentMetrics()

    app = FastAPI(
        title="Payment API",
        version="1.0.0",
        description=(
            "A runnable payment service with metrics, structured logs, "
            "idempotency, and controlled fault injection."
        ),
    )
    app.state.config = service_config
    app.state.database = database
    app.state.fault_controller = fault_controller
    app.state.metrics = metrics

    def require_fault_admin(
        x_admin_token: str | None = Header(default=None),
    ) -> None:
        if not service_config.fault_injection_enabled:
            raise HTTPException(
                status_code=404,
                detail="fault injection is disabled",
            )
        expected = service_config.fault_admin_token
        if (
            not expected
            or not x_admin_token
            or not secrets.compare_digest(x_admin_token, expected)
        ):
            raise HTTPException(
                status_code=403,
                detail="invalid admin token",
            )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        request_id = (
            request.headers.get("x-request-id")
            or f"req_{uuid.uuid4().hex}"
        )
        request.state.request_id = request_id
        started_at = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            elapsed = time.perf_counter() - started_at
            metrics.http_requests.labels(
                service=SERVICE_NAME,
                method=request.method,
                path=path,
                status=str(status),
            ).inc()
            metrics.http_duration.labels(
                service=SERVICE_NAME,
                method=request.method,
                path=path,
            ).observe(elapsed)
            event_logger.emit(
                "ERROR" if status >= 500 else "INFO",
                "http_request",
                request_id=request_id,
                method=request.method,
                path=path,
                status=status,
                latency_ms=round(elapsed * 1000, 2),
            )

    @app.exception_handler(HTTPException)
    async def handle_http_error(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        if exc.status_code >= 500:
            event_logger.emit(
                "ERROR",
                "payment request failed",
                request_id=getattr(
                    request.state,
                    "request_id",
                    None,
                ),
                path=request.url.path,
                status=exc.status_code,
                error=exc.detail,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "detail": exc.detail,
                "request_id": getattr(
                    request.state,
                    "request_id",
                    None,
                ),
            },
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "environment": service_config.environment,
            "fault_injection_enabled": (
                service_config.fault_injection_enabled
            ),
        }

    @app.get("/ready")
    def ready() -> dict[str, str]:
        try:
            database.ping()
        except sqlite3.Error as exc:
            raise HTTPException(
                status_code=503,
                detail="payment database is unavailable",
            ) from exc
        return {"status": "ready", "service": SERVICE_NAME}

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        return Response(
            generate_latest(metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.post("/pay")
    async def pay(
        payload: PayRequest,
        request: Request,
    ) -> dict[str, Any]:
        state = fault_controller.snapshot()
        if state["latency_enabled"]:
            await asyncio.sleep(state["delay_ms"] / 1000)

        if (
            state["error_5xx_enabled"]
            and random.random() < state["error_5xx_ratio"]
        ):
            metrics.payment_requests.labels(
                channel=payload.channel,
                result="failed",
            ).inc()
            metrics.payment_failures.labels(
                reason="injected_5xx"
            ).inc()
            raise HTTPException(
                status_code=500,
                detail={
                    "error_type": "InjectedServerError",
                    "message": "controlled 5xx fault is enabled",
                },
            )

        if (
            state["channel_failure_enabled"]
            and payload.channel == state["channel"]
            and random.random() < state["channel_ratio"]
        ):
            metrics.payment_requests.labels(
                channel=payload.channel,
                result="failed",
            ).inc()
            metrics.payment_failures.labels(
                reason="channel_failure"
            ).inc()
            raise HTTPException(
                status_code=502,
                detail={
                    "error_type": "InjectedChannelFailure",
                    "message": (
                        f"controlled {payload.channel} channel "
                        "failure is enabled"
                    ),
                },
            )

        record, replayed = database.create_or_get(payload)
        metrics.payment_requests.labels(
            channel=payload.channel,
            result="idempotent" if replayed else "paid",
        ).inc()
        event_logger.emit(
            "INFO",
            "payment completed",
            request_id=request.state.request_id,
            order_id=payload.order_id,
            payment_id=record["payment_id"],
            channel=payload.channel,
            amount=payload.amount,
            currency=payload.currency,
            idempotent_replay=replayed,
        )
        return {
            "success": True,
            "idempotent_replay": replayed,
            **record,
            "request_id": request.state.request_id,
        }

    @app.get("/payments/{payment_id}")
    def payment(payment_id: str) -> dict[str, Any]:
        record = database.get(payment_id)
        if not record:
            raise HTTPException(
                status_code=404,
                detail="payment not found",
            )
        return record

    @app.post("/refund")
    def refund(payload: RefundRequest) -> dict[str, Any]:
        record = database.refund(
            payload.payment_id,
            payload.reason,
        )
        if not record:
            raise HTTPException(
                status_code=409,
                detail=(
                    "payment does not exist or is not refundable"
                ),
            )
        event_logger.emit(
            "INFO",
            "payment refunded",
            payment_id=payload.payment_id,
            reason=payload.reason,
        )
        return {"success": True, **record}

    @app.get(
        "/admin/fault/state",
        dependencies=[Depends(require_fault_admin)],
    )
    def fault_state() -> dict[str, Any]:
        return fault_controller.snapshot()

    @app.post(
        "/admin/fault/5xx",
        dependencies=[Depends(require_fault_admin)],
    )
    def set_5xx(payload: Fault5xxRequest) -> dict[str, Any]:
        state = fault_controller.set_5xx(payload)
        metrics.sync_faults(state)
        event_logger.emit(
            "WARNING",
            "5xx fault updated",
            fault_state=state,
        )
        return {"ok": True, "fault_state": state}

    @app.post(
        "/admin/fault/latency",
        dependencies=[Depends(require_fault_admin)],
    )
    def set_latency(
        payload: FaultLatencyRequest,
    ) -> dict[str, Any]:
        state = fault_controller.set_latency(payload)
        metrics.sync_faults(state)
        event_logger.emit(
            "WARNING",
            "latency fault updated",
            fault_state=state,
        )
        return {"ok": True, "fault_state": state}

    @app.post(
        "/admin/fault/channel",
        dependencies=[Depends(require_fault_admin)],
    )
    def set_channel(
        payload: FaultChannelRequest,
    ) -> dict[str, Any]:
        state = fault_controller.set_channel(payload)
        metrics.sync_faults(state)
        event_logger.emit(
            "WARNING",
            "channel fault updated",
            fault_state=state,
        )
        return {"ok": True, "fault_state": state}

    @app.post(
        "/admin/fault/reset",
        dependencies=[Depends(require_fault_admin)],
    )
    def reset_faults() -> dict[str, Any]:
        state = fault_controller.reset()
        metrics.sync_faults(state)
        event_logger.emit(
            "WARNING",
            "all fault injections reset",
            fault_state=state,
        )
        return {"ok": True, "fault_state": state}

    return app


app = create_payment_app()

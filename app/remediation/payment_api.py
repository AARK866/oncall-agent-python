import logging
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.observability.metrics import observe_tool_call
from app.reliability import (
    RetryError,
    RetryPolicy,
    run_with_retry,
)
from app.schemas import ToolResult


logger = logging.getLogger(__name__)

_ALLOWED_ALERTS = {
    "PaymentApiHigh5xxRatio": (
        "Reset the controlled payment-api 5xx fault."
    ),
    "PaymentApiHighP95Latency": (
        "Reset the controlled payment-api latency fault."
    ),
}


class RemediationPlan(BaseModel):
    action: Literal["reset_payment_faults"]
    service: Literal["payment-api"]
    alert_name: str
    reason: str
    risk_reason: str = (
        "This action changes payment-api runtime fault state and "
        "requires explicit human approval."
    )
    arguments: dict[str, Any] = Field(default_factory=dict)


class PaymentApiRemediationController:
    connector_name = "approved_payment_remediation"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        base_url: str | None = None,
        admin_token: str | None = None,
        timeout_seconds: int | None = None,
        verify_ssl: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.enabled = (
            settings.payment_api_remediation_enabled
            if enabled is None
            else enabled
        )
        self.base_url = (
            base_url
            if base_url is not None
            else settings.payment_api_base_url
        )
        self.admin_token = (
            admin_token
            if admin_token is not None
            else settings.payment_api_fault_admin_token
        )
        self.timeout_seconds = (
            timeout_seconds
            or settings.payment_api_timeout_seconds
        )
        self.verify_ssl = (
            settings.payment_api_verify_ssl
            if verify_ssl is None
            else verify_ssl
        )
        self.transport = transport
        self.retry_policy = (
            retry_policy or RetryPolicy.from_settings()
        )

    def plan(
        self,
        *,
        service: str,
        labels: dict[str, str],
        trigger_metadata: dict[str, Any],
    ) -> RemediationPlan | None:
        if not self.enabled or service != "payment-api":
            return None
        if trigger_metadata.get("source") != "alertmanager":
            return None

        alert_name = str(labels.get("alertname") or "")
        reason = _ALLOWED_ALERTS.get(alert_name)
        if reason is None:
            return None

        return RemediationPlan(
            action="reset_payment_faults",
            service="payment-api",
            alert_name=alert_name,
            reason=reason,
            arguments={},
        )

    async def execute(
        self,
        plan: RemediationPlan,
    ) -> ToolResult:
        started_at = perf_counter()
        configuration_error = self._configuration_error(plan)
        if configuration_error:
            return self._failure_result(
                started_at,
                configuration_error,
                attempts=0,
            )

        try:
            outcome = await run_with_retry(
                operation=self._reset_faults,
                policy=self.retry_policy,
            )
        except RetryError as exc:
            return self._failure_result(
                started_at,
                str(exc.last_error),
                attempts=exc.attempts,
                retry=exc.metadata(),
            )

        elapsed_ms = int((perf_counter() - started_at) * 1000)
        data = {
            "provider": "payment-api",
            "action": plan.action,
            "service": plan.service,
            "alert_name": plan.alert_name,
            "endpoint": "/admin/fault/reset",
            "fault_state": outcome.value,
            "summary": (
                "Approved payment-api fault reset completed."
            ),
            "_retry": outcome.metadata(),
        }
        observe_tool_call(
            tool_name=plan.action,
            connector=self.connector_name,
            success=True,
            duration_seconds=elapsed_ms / 1000,
        )
        logger.info(
            "Approved remediation completed.",
            extra={
                "event": "remediation.execution",
                "outcome": "success",
                "action": plan.action,
                "service": plan.service,
                "duration_ms": elapsed_ms,
            },
        )
        return ToolResult(
            tool_name=plan.action,
            success=True,
            data=data,
            elapsed_ms=elapsed_ms,
        )

    async def _reset_faults(self) -> dict[str, Any]:
        base_url = _normalized_url(self.base_url)
        headers = {
            "X-Admin-Token": str(self.admin_token),
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{base_url}/admin/fault/reset",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError(
                "payment-api returned an invalid reset response."
            )
        fault_state = payload.get("fault_state")
        if not isinstance(fault_state, dict):
            raise RuntimeError(
                "payment-api reset response has no fault_state."
            )
        enabled_flags = (
            "error_5xx_enabled",
            "latency_enabled",
            "channel_failure_enabled",
        )
        if any(bool(fault_state.get(flag)) for flag in enabled_flags):
            raise RuntimeError(
                "payment-api still reports an enabled fault after reset."
            )
        return fault_state

    def _configuration_error(
        self,
        plan: RemediationPlan,
    ) -> str | None:
        if not self.enabled:
            return "Payment remediation is disabled."
        if (
            plan.action != "reset_payment_faults"
            or plan.service != "payment-api"
            or plan.alert_name not in _ALLOWED_ALERTS
        ):
            return "Remediation action is not allowed by policy."
        try:
            _normalized_url(self.base_url)
        except ValueError as exc:
            return str(exc)
        if (
            not self.admin_token
            or len(self.admin_token) < 16
        ):
            return (
                "PAYMENT_API_FAULT_ADMIN_TOKEN must contain at "
                "least 16 characters."
            )
        return None

    def _failure_result(
        self,
        started_at: float,
        error: str,
        *,
        attempts: int,
        retry: dict[str, Any] | None = None,
    ) -> ToolResult:
        elapsed_ms = int((perf_counter() - started_at) * 1000)
        data: dict[str, Any] = {
            "provider": "payment-api",
            "action": "reset_payment_faults",
            "attempts": attempts,
        }
        if retry:
            data["_retry"] = retry
        observe_tool_call(
            tool_name="reset_payment_faults",
            connector=self.connector_name,
            success=False,
            duration_seconds=elapsed_ms / 1000,
        )
        logger.warning(
            "Approved remediation failed.",
            extra={
                "event": "remediation.execution",
                "outcome": "failure",
                "action": "reset_payment_faults",
                "duration_ms": elapsed_ms,
            },
        )
        return ToolResult(
            tool_name="reset_payment_faults",
            success=False,
            data=data,
            error=error,
            elapsed_ms=elapsed_ms,
        )


def _normalized_url(value: str | None) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "PAYMENT_API_BASE_URL must be an HTTP(S) origin."
        )
    return normalized

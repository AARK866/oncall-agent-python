from datetime import datetime
from typing import Any

from fastapi import APIRouter

from app.agents import OpsAgent
from app.schemas import (
    AlertAnalyzeRequest,
    AlertSeverity,
    AlertTriggerResponse,
    AlertmanagerAlert,
    AlertmanagerWebhookRequest,
    ChatResponse,
)
from app.storage import SQLiteIncidentStore

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("/analyze", response_model=AlertTriggerResponse)
async def analyze_alert(request: AlertAnalyzeRequest) -> AlertTriggerResponse:
    labels = dict(request.labels)
    labels.setdefault("service", request.service)
    labels.setdefault("severity", request.severity.value)

    response = await _ops_agent().analyze(
        question=_build_alert_question(
            title=request.title,
            service=request.service,
            severity=request.severity,
            labels=labels,
            annotations=request.annotations,
            start_time=request.start_time,
        ),
        session_id=_session_id("alert", request.alert_id),
        service=request.service,
        severity=request.severity,
        labels=labels,
        trigger_metadata={
            "source": "api_alert",
            "alert_id": request.alert_id,
            "title": request.title,
            "service": request.service,
            "severity": request.severity.value,
            "start_time": _datetime_text(request.start_time),
            "labels": labels,
            "annotations": request.annotations,
        },
    )

    return AlertTriggerResponse(
        received=1,
        processed=1,
        results=[response],
        metadata={"source": "api_alert"},
    )


@router.post("/alertmanager", response_model=AlertTriggerResponse)
async def receive_alertmanager_webhook(
    request: AlertmanagerWebhookRequest,
) -> AlertTriggerResponse:
    agent = _ops_agent()
    results: list[ChatResponse] = []
    firing_alerts = [
        (index, alert)
        for index, alert in enumerate(request.alerts)
        if _is_firing(alert)
    ]

    for index, alert in firing_alerts:
        labels = {**request.common_labels, **alert.labels}
        annotations = {**request.common_annotations, **alert.annotations}
        severity = _severity_from_labels(labels)
        service = _service_from_labels(labels)
        title = _alert_title(labels, annotations)
        alert_id = alert.fingerprint or f"{request.group_key or 'alert'}-{index}"

        results.append(
            await agent.analyze(
                question=_build_alert_question(
                    title=title,
                    service=service,
                    severity=severity,
                    labels=labels,
                    annotations=annotations,
                    start_time=alert.starts_at,
                ),
                session_id=_session_id("alertmanager", alert_id),
                service=service,
                severity=severity,
                labels=labels,
                trigger_metadata=_alertmanager_trigger_metadata(
                    request=request,
                    alert=alert,
                    alert_id=alert_id,
                    title=title,
                    service=service,
                    severity=severity,
                    labels=labels,
                    annotations=annotations,
                ),
            )
        )

    return AlertTriggerResponse(
        received=len(request.alerts),
        processed=len(results),
        results=results,
        metadata={
            "source": "alertmanager",
            "receiver": request.receiver,
            "status": request.status,
            "group_key": request.group_key,
            "ignored": len(request.alerts) - len(results),
        },
    )


def _ops_agent() -> OpsAgent:
    return OpsAgent.create_default(incident_store=SQLiteIncidentStore.from_settings())


def _is_firing(alert: AlertmanagerAlert) -> bool:
    return alert.status.strip().lower() == "firing"


def _alert_title(labels: dict[str, str], annotations: dict[str, str]) -> str:
    return (
        labels.get("alertname")
        or annotations.get("summary")
        or annotations.get("title")
        or "Alertmanager alert"
    )


def _service_from_labels(labels: dict[str, str]) -> str | None:
    for key in ("service", "app", "application", "job"):
        value = labels.get(key)
        if value:
            return value
    return None


def _severity_from_labels(labels: dict[str, str]) -> AlertSeverity:
    raw = (labels.get("severity") or labels.get("priority") or "").strip().lower()
    if raw in {"critical", "crit", "page", "p0", "p1"}:
        return AlertSeverity.critical
    if raw in {"info", "informational", "notice", "p4"}:
        return AlertSeverity.info
    return AlertSeverity.warning


def _build_alert_question(
    title: str,
    service: str | None,
    severity: AlertSeverity,
    labels: dict[str, str],
    annotations: dict[str, str],
    start_time: datetime | None,
) -> str:
    parts = [
        f"Alert {title} is firing.",
        f"Service: {service or 'unknown'}.",
        f"Severity: {severity.value}.",
    ]

    summary = annotations.get("summary")
    description = annotations.get("description")
    if summary:
        parts.append(f"Summary: {summary}.")
    if description:
        parts.append(f"Description: {description}.")
    if start_time:
        parts.append(f"Started at: {_datetime_text(start_time)}.")
    if labels:
        parts.append(f"Labels: {_format_kv(labels)}.")

    return " ".join(parts)


def _alertmanager_trigger_metadata(
    request: AlertmanagerWebhookRequest,
    alert: AlertmanagerAlert,
    alert_id: str,
    title: str,
    service: str | None,
    severity: AlertSeverity,
    labels: dict[str, str],
    annotations: dict[str, str],
) -> dict[str, Any]:
    return {
        "source": "alertmanager",
        "alert_id": alert_id,
        "title": title,
        "service": service,
        "severity": severity.value,
        "status": alert.status,
        "starts_at": _datetime_text(alert.starts_at),
        "ends_at": _datetime_text(alert.ends_at),
        "fingerprint": alert.fingerprint,
        "generator_url": alert.generator_url,
        "receiver": request.receiver,
        "group_key": request.group_key,
        "external_url": request.external_url,
        "labels": labels,
        "annotations": annotations,
    }


def _session_id(prefix: str, raw_id: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in raw_id)
    return f"{prefix}-{safe[:80] or 'unknown'}"


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _format_kv(values: dict[str, str]) -> str:
    return ", ".join(f"{key}={values[key]}" for key in sorted(values))

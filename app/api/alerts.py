from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.schemas import (
    AlertGroupRecord,
    AlertAnalyzeRequest,
    AlertSeverity,
    AlertTriggerResponse,
    AlertmanagerAlert,
    AlertmanagerWebhookRequest,
)
from app.security import require_api_token, require_webhook_auth
from app.tasks import (
    DiagnosisTaskQueue,
    DiagnosisTaskSubmission,
    TaskDispatcher,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post(
    "/analyze",
    response_model=AlertTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def analyze_alert(
    request: AlertAnalyzeRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_token),
) -> AlertTriggerResponse:
    labels = dict(request.labels)
    labels.setdefault("service", request.service)
    labels.setdefault("severity", request.severity.value)
    title = request.title
    annotations = request.annotations

    submission = _submit_background_diagnosis(
        background_tasks=background_tasks,
        queue=DiagnosisTaskQueue(),
        dedupe_key=_api_alert_dedupe_key(request.alert_id),
        source="api_alert",
        question=_build_alert_question(
            title=title,
            service=request.service,
            severity=request.severity,
            labels=labels,
            annotations=annotations,
            start_time=request.start_time,
        ),
        session_id=_session_id("alert", request.alert_id),
        title=title,
        service=request.service,
        severity=request.severity,
        labels=labels,
        annotations=annotations,
        trigger_metadata={
            "source": "api_alert",
            "alert_id": request.alert_id,
            "title": title,
            "service": request.service,
            "severity": request.severity.value,
            "start_time": _datetime_text(request.start_time),
            "labels": labels,
            "annotations": annotations,
        },
    )

    return AlertTriggerResponse(
        received=1,
        processed=1,
        tasks=[submission.task],
        metadata=_submission_metadata("api_alert", [submission]),
    )


@router.post(
    "/alertmanager",
    response_model=AlertTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_alertmanager_webhook(
    request: AlertmanagerWebhookRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_webhook_auth),
) -> AlertTriggerResponse:
    queue = DiagnosisTaskQueue()
    submissions: list[DiagnosisTaskSubmission] = []
    resolved_groups: list[AlertGroupRecord] = []
    firing_alerts = [
        (index, alert)
        for index, alert in enumerate(request.alerts)
        if _is_firing(alert)
    ]

    for index, alert in enumerate(request.alerts):
        if _is_firing(alert):
            continue
        labels = {**request.common_labels, **alert.labels}
        dedupe_key = _alertmanager_dedupe_key(request=request, alert=alert, labels=labels, index=index)
        resolved = queue.resolve_alert(dedupe_key)
        if resolved:
            resolved_groups.append(resolved)

    for index, alert in firing_alerts:
        labels = {**request.common_labels, **alert.labels}
        annotations = {**request.common_annotations, **alert.annotations}
        severity = _severity_from_labels(labels)
        service = _service_from_labels(labels)
        title = _alert_title(labels, annotations)
        alert_id = alert.fingerprint or f"{request.group_key or 'alert'}-{index}"
        dedupe_key = _alertmanager_dedupe_key(request=request, alert=alert, labels=labels, index=index)

        submissions.append(
            _submit_background_diagnosis(
                background_tasks=background_tasks,
                queue=queue,
                dedupe_key=dedupe_key,
                source="alertmanager",
                question=_build_alert_question(
                    title=title,
                    service=service,
                    severity=severity,
                    labels=labels,
                    annotations=annotations,
                    start_time=alert.starts_at,
                ),
                session_id=_session_id("alertmanager", alert_id),
                title=title,
                service=service,
                severity=severity,
                labels=labels,
                annotations=annotations,
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
        processed=len(submissions),
        tasks=[submission.task for submission in submissions],
        metadata={
            **_submission_metadata("alertmanager", submissions),
            "source": "alertmanager",
            "receiver": request.receiver,
            "status": request.status,
            "group_key": request.group_key,
            "ignored": len(request.alerts) - len(submissions),
            "resolved": len(resolved_groups),
            "resolved_group_ids": [group.group_id for group in resolved_groups],
        },
    )


@router.get("/groups", response_model=list[AlertGroupRecord])
async def list_alert_groups(
    limit: int = 20,
    _: None = Depends(require_api_token),
) -> list[AlertGroupRecord]:
    return DiagnosisTaskQueue().list_alert_groups(limit=limit)


@router.get("/groups/{group_id}", response_model=AlertGroupRecord)
async def get_alert_group(
    group_id: str,
    _: None = Depends(require_api_token),
) -> AlertGroupRecord:
    group = DiagnosisTaskQueue().get_alert_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Alert group not found")
    return group


def _submit_background_diagnosis(
    background_tasks: BackgroundTasks,
    queue: DiagnosisTaskQueue,
    dedupe_key: str,
    source: str,
    question: str,
    session_id: str,
    title: str,
    service: str | None,
    severity: AlertSeverity,
    labels: dict[str, str],
    annotations: dict[str, str],
    trigger_metadata: dict[str, Any],
) -> DiagnosisTaskSubmission:
    submission = queue.submit_alert(
        dedupe_key=dedupe_key,
        source=source,
        title=title,
        question=question,
        session_id=session_id,
        service=service,
        severity=severity,
        labels=labels,
        annotations=annotations,
        trigger_metadata=trigger_metadata,
    )
    if submission.scheduled:
        TaskDispatcher().dispatch_diagnosis(
            submission.task.task_id,
            background_tasks,
        )
    return submission


def _submission_metadata(
    source: str,
    submissions: list[DiagnosisTaskSubmission],
) -> dict[str, Any]:
    return {
        "source": source,
        "scheduled": sum(1 for submission in submissions if submission.scheduled),
        "deduplicated": sum(1 for submission in submissions if submission.deduplicated),
        "alert_group_ids": [
            submission.alert_group.group_id
            for submission in submissions
            if submission.alert_group is not None
        ],
    }


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


def _api_alert_dedupe_key(alert_id: str) -> str:
    return f"api_alert:alert_id:{alert_id}"


def _alertmanager_dedupe_key(
    request: AlertmanagerWebhookRequest,
    alert: AlertmanagerAlert,
    labels: dict[str, str],
    index: int,
) -> str:
    if alert.fingerprint:
        return f"alertmanager:fingerprint:{alert.fingerprint}"
    if request.group_key:
        return f"alertmanager:group:{request.group_key}:index:{index}"
    return f"alertmanager:labels:{_format_kv(labels)}"


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

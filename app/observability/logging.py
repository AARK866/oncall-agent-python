from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.observability.context import current_trace_id
from app.security_context import current_principal, current_tenant_id

_EXTRA_FIELDS = (
    "event",
    "outcome",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "tool_name",
    "task_kind",
    "task_id",
    "resource_type",
    "resource_id",
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        principal = current_principal()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
            "service": getattr(
                record,
                "service",
                settings.telemetry_service_name,
            ),
            "trace_id": current_trace_id(),
            "tenant_id": current_tenant_id(),
            "actor": principal.subject if principal else None,
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = _redact(
                self.formatException(record.exc_info)
            )
        return json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )


def configure_logging() -> None:
    level = getattr(
        logging,
        settings.log_level.strip().upper(),
        logging.INFO,
    )
    root = logging.getLogger()
    root.setLevel(level)
    if settings.log_format.strip().lower() != "json":
        return

    formatter = JsonLogFormatter()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(formatter)
    _configure_file_handler(root, formatter)


def _configure_file_handler(
    root: logging.Logger,
    formatter: logging.Formatter,
) -> None:
    configured_path = settings.log_file_path
    existing_handlers = [
        handler
        for handler in root.handlers
        if getattr(handler, "_oncall_file_handler", False)
    ]
    if not configured_path:
        for handler in existing_handlers:
            root.removeHandler(handler)
            handler.close()
        return

    path = Path(configured_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in existing_handlers:
        if Path(handler.baseFilename) == path:
            handler.setFormatter(formatter)
            return
        root.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        path,
        maxBytes=max(1, settings.log_file_max_bytes),
        backupCount=max(1, settings.log_file_backup_count),
        encoding="utf-8",
    )
    file_handler._oncall_file_handler = True
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _redact(value: str) -> str:
    from app.security import redact_text

    return redact_text(value)

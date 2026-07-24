import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def main() -> int:
    args = _parse_args()
    log_path = args.log_file or (
        Path(settings.log_file_path)
        if settings.log_file_path
        else None
    )
    if log_path is None:
        raise SystemExit(
            "--log-file or LOG_FILE_PATH is required"
        )
    if not settings.loki_base_url:
        raise SystemExit("LOKI_BASE_URL is required")

    headers: dict[str, str] = {}
    if settings.loki_bearer_token:
        headers["Authorization"] = (
            f"Bearer {settings.loki_bearer_token}"
        )
    if settings.loki_org_id:
        headers["X-Scope-OrgID"] = settings.loki_org_id
    auth = (
        (settings.loki_username, settings.loki_password or "")
        if settings.loki_username
        else None
    )

    shipped = 0
    with httpx.Client(
        timeout=settings.loki_timeout_seconds,
        headers=headers,
        auth=auth,
        verify=settings.loki_verify_ssl,
    ) as client:
        with _wait_for_file(log_path, follow=args.follow).open(
            "r",
            encoding="utf-8",
        ) as log_file:
            if args.follow and not args.from_start:
                log_file.seek(0, 2)
            while True:
                lines = _read_batch(log_file, args.batch_size)
                if lines:
                    streams = build_loki_streams(
                        lines,
                        default_service=settings.telemetry_service_name,
                    )
                    if streams:
                        _push(
                            client,
                            settings.loki_base_url,
                            streams,
                        )
                        batch_count = sum(
                            len(stream["values"]) for stream in streams
                        )
                        shipped += batch_count
                        print(
                            f"Shipped {batch_count} log line(s) to Loki.",
                            flush=True,
                        )
                    continue
                if not args.follow:
                    break
                time.sleep(args.poll_interval)

    print(f"Loki log shipping complete: {shipped} line(s).")
    return 0


def build_loki_streams(
    lines: list[str],
    *,
    default_service: str,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str],
        list[list[str]],
    ] = defaultdict(list)
    now_ns = time.time_ns()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        payload = _json_payload(line)
        service = _label(
            payload.get("service"),
            default_service,
        )
        level = _label(payload.get("level"), "UNKNOWN")
        logger_name = _label(payload.get("logger"), "unknown")
        timestamp_ns = _timestamp_ns(
            payload.get("timestamp"),
            fallback=now_ns + index,
        )
        grouped[(service, level, logger_name)].append(
            [str(timestamp_ns), line]
        )

    streams = []
    for (service, level, logger_name), values in grouped.items():
        values.sort(key=lambda item: int(item[0]))
        streams.append(
            {
                "stream": {
                    "service": service,
                    "level": level,
                    "logger": logger_name,
                },
                "values": values,
            }
        )
    return streams


def _push(
    client: httpx.Client,
    base_url: str,
    streams: list[dict[str, Any]],
) -> None:
    response = client.post(
        f"{base_url.rstrip('/')}/loki/api/v1/push",
        json={"streams": streams},
    )
    response.raise_for_status()


def _read_batch(log_file, batch_size: int) -> list[str]:
    lines = []
    for _ in range(max(1, batch_size)):
        line = log_file.readline()
        if not line:
            break
        lines.append(line)
    return lines


def _wait_for_file(path: Path, *, follow: bool) -> Path:
    while not path.exists():
        if not follow:
            raise FileNotFoundError(path)
        time.sleep(0.5)
    return path


def _json_payload(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _timestamp_ns(value: Any, *, fallback: int) -> int:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _label(value: Any, fallback: str) -> str:
    normalized = str(value or fallback).strip()
    return normalized[:200] or fallback


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ship OnCall Agent JSON file logs to Loki."
    )
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--from-start", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())

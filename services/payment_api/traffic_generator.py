import argparse
import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx


def emit(level: str, message: str, **fields: Any) -> None:
    print(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "logger": "payment-traffic-generator",
                "service": "payment-traffic-generator",
                "message": message,
                **fields,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


async def send_payment(
    client: httpx.AsyncClient,
    target: str,
    semaphore: asyncio.Semaphore,
) -> None:
    order_id = f"ord_{uuid.uuid4().hex[:14]}"
    payload = {
        "order_id": order_id,
        "user_id": f"user_{random.randint(1, 100)}",
        "amount": random.choice([500, 1200, 2999, 5000]),
        "currency": random.choice(["CNY", "JPY"]),
        "channel": random.choice(["card", "wallet", "bank"]),
    }
    async with semaphore:
        try:
            response = await client.post(
                f"{target}/pay",
                json=payload,
                headers={
                    "X-Request-ID": f"load_{uuid.uuid4().hex}",
                },
            )
            emit(
                "ERROR" if response.status_code >= 500 else "INFO",
                "payment request sent",
                order_id=order_id,
                status=response.status_code,
                channel=payload["channel"],
            )
        except httpx.HTTPError as exc:
            emit(
                "ERROR",
                "payment transport failed",
                order_id=order_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )


async def run(
    *,
    target: str,
    requests_per_second: float,
    total_requests: int | None,
    max_in_flight: int,
) -> None:
    target = target.rstrip("/")
    interval = 1 / max(requests_per_second, 0.1)
    semaphore = asyncio.Semaphore(max(1, max_in_flight))
    pending: set[asyncio.Task[None]] = set()
    sent = 0
    emit(
        "INFO",
        "payment traffic generator started",
        target=target,
        requests_per_second=requests_per_second,
        total_requests=total_requests,
    )
    async with httpx.AsyncClient(timeout=35) as client:
        while total_requests is None or sent < total_requests:
            task = asyncio.create_task(
                send_payment(client, target, semaphore)
            )
            pending.add(task)
            task.add_done_callback(pending.discard)
            sent += 1
            await asyncio.sleep(interval)
        if pending:
            await asyncio.gather(*pending)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate controlled payment-api traffic."
    )
    parser.add_argument(
        "--target",
        default=os.getenv(
            "PAYMENT_API_TARGET",
            "http://127.0.0.1:8010",
        ),
    )
    parser.add_argument(
        "--rps",
        type=float,
        default=float(os.getenv("PAYMENT_TRAFFIC_RPS", "3")),
    )
    parser.add_argument(
        "--requests",
        type=int,
        help="Stop after this many requests. Omit to run continuously.",
    )
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=100,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    asyncio.run(
        run(
            target=args.target,
            requests_per_second=args.rps,
            total_requests=args.requests,
            max_in_flight=args.max_in_flight,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

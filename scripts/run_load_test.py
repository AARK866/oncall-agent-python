import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class LoadTestConfig:
    target_url: str
    path: str = "/health"
    method: str = "GET"
    requests: int = 200
    concurrency: int = 20
    timeout_seconds: float = 10.0
    max_error_rate: float = 0.01
    max_p95_ms: float = 500.0
    min_requests_per_second: float = 0.0
    warmup_requests: int = 5
    json_body: dict[str, Any] | None = None
    api_token: str | None = None


@dataclass(frozen=True)
class LoadSample:
    duration_ms: float
    status_code: int | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.error is None
            and self.status_code is not None
            and 200 <= self.status_code < 400
        )


@dataclass(frozen=True)
class LoadTestSummary:
    passed: bool
    requests: int
    succeeded: int
    failed: int
    error_rate: float
    elapsed_seconds: float
    requests_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    status_codes: dict[str, int]
    errors: dict[str, int]
    thresholds: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def run_load_test(
    config: LoadTestConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LoadTestSummary:
    _validate_config(config)
    headers = {"User-Agent": "oncall-agent-load-test/1.0"}
    if config.api_token:
        headers["X-API-Key"] = config.api_token

    limits = httpx.Limits(
        max_connections=config.concurrency,
        max_keepalive_connections=config.concurrency,
    )
    async with httpx.AsyncClient(
        base_url=config.target_url.rstrip("/"),
        timeout=config.timeout_seconds,
        headers=headers,
        limits=limits,
        transport=transport,
    ) as client:
        for _ in range(config.warmup_requests):
            await _request_once(client, config)

        queue: asyncio.Queue[int] = asyncio.Queue()
        for request_number in range(config.requests):
            queue.put_nowait(request_number)

        samples: list[LoadSample] = []
        started_at = perf_counter()
        workers = [
            asyncio.create_task(_worker(queue, client, config, samples))
            for _ in range(min(config.concurrency, config.requests))
        ]
        await queue.join()
        await asyncio.gather(*workers)
        elapsed_seconds = perf_counter() - started_at

    return summarize_samples(
        samples,
        elapsed_seconds=elapsed_seconds,
        max_error_rate=config.max_error_rate,
        max_p95_ms=config.max_p95_ms,
        min_requests_per_second=config.min_requests_per_second,
    )


async def _worker(
    queue: asyncio.Queue[int],
    client: httpx.AsyncClient,
    config: LoadTestConfig,
    samples: list[LoadSample],
) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            samples.append(await _request_once(client, config))
        finally:
            queue.task_done()


async def _request_once(
    client: httpx.AsyncClient,
    config: LoadTestConfig,
) -> LoadSample:
    started_at = perf_counter()
    try:
        response = await client.request(
            config.method.upper(),
            config.path,
            json=config.json_body,
        )
        return LoadSample(
            duration_ms=(perf_counter() - started_at) * 1000,
            status_code=response.status_code,
        )
    except Exception as exc:
        return LoadSample(
            duration_ms=(perf_counter() - started_at) * 1000,
            status_code=None,
            error=exc.__class__.__name__,
        )


def summarize_samples(
    samples: list[LoadSample],
    *,
    elapsed_seconds: float,
    max_error_rate: float,
    max_p95_ms: float,
    min_requests_per_second: float = 0.0,
) -> LoadTestSummary:
    durations = sorted(sample.duration_ms for sample in samples)
    succeeded = sum(sample.succeeded for sample in samples)
    failed = len(samples) - succeeded
    error_rate = failed / len(samples) if samples else 1.0
    requests_per_second = (
        len(samples) / elapsed_seconds if elapsed_seconds > 0 else 0.0
    )
    p95_ms = _percentile(durations, 0.95)
    passed = (
        bool(samples)
        and error_rate <= max_error_rate
        and p95_ms <= max_p95_ms
        and requests_per_second >= min_requests_per_second
    )
    return LoadTestSummary(
        passed=passed,
        requests=len(samples),
        succeeded=succeeded,
        failed=failed,
        error_rate=error_rate,
        elapsed_seconds=elapsed_seconds,
        requests_per_second=requests_per_second,
        p50_ms=_percentile(durations, 0.50),
        p95_ms=p95_ms,
        p99_ms=_percentile(durations, 0.99),
        status_codes=dict(
            sorted(
                Counter(
                    str(sample.status_code)
                    for sample in samples
                    if sample.status_code is not None
                ).items()
            )
        ),
        errors=dict(
            sorted(
                Counter(
                    sample.error
                    for sample in samples
                    if sample.error is not None
                ).items()
            )
        ),
        thresholds={
            "max_error_rate": max_error_rate,
            "max_p95_ms": max_p95_ms,
            "min_requests_per_second": min_requests_per_second,
        },
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]


def _validate_config(config: LoadTestConfig) -> None:
    if config.requests <= 0:
        raise ValueError("requests must be greater than zero")
    if config.concurrency <= 0:
        raise ValueError("concurrency must be greater than zero")
    if not 0 <= config.max_error_rate <= 1:
        raise ValueError("max_error_rate must be between zero and one")
    if config.max_p95_ms <= 0:
        raise ValueError("max_p95_ms must be greater than zero")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a threshold-based HTTP load test."
    )
    parser.add_argument("--target-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--method", choices=["GET", "POST"], default="GET")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    parser.add_argument("--min-rps", type=float, default=0.0)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--body-file", type=Path)
    parser.add_argument(
        "--api-token-env",
        default="LOAD_TEST_API_TOKEN",
        help="Environment variable containing the API token.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _load_json_body(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("body file must contain a JSON object")
    return data


def _print_summary(summary: LoadTestSummary, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary.to_dict(), indent=2))
        return
    print("OnCall Agent load test")
    print(f"- status: {'PASS' if summary.passed else 'FAIL'}")
    print(
        f"- requests: {summary.requests} "
        f"({summary.succeeded} succeeded, {summary.failed} failed)"
    )
    print(f"- error rate: {summary.error_rate:.2%}")
    print(f"- throughput: {summary.requests_per_second:.2f} req/s")
    print(
        f"- latency: p50={summary.p50_ms:.2f} ms, "
        f"p95={summary.p95_ms:.2f} ms, p99={summary.p99_ms:.2f} ms"
    )
    print(f"- status codes: {summary.status_codes}")
    if summary.errors:
        print(f"- errors: {summary.errors}")


async def main() -> int:
    args = _parse_args()
    config = LoadTestConfig(
        target_url=args.target_url,
        path=args.path,
        method=args.method,
        requests=args.requests,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        max_error_rate=args.max_error_rate,
        max_p95_ms=args.max_p95_ms,
        min_requests_per_second=args.min_rps,
        warmup_requests=max(0, args.warmup),
        json_body=_load_json_body(args.body_file),
        api_token=os.getenv(args.api_token_env),
    )
    summary = await run_load_test(config)
    _print_summary(summary, as_json=args.json)
    return 0 if summary.passed else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))

"""Bounded LynxPay load scenarios with machine-readable performance evidence."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from typing import Any
import uuid

import httpx

WRITE_SCENARIOS = {"callback-burst", "duplicate-callback", "concurrent-stk"}


@dataclass
class Result:
    scenario: str
    target: str
    started_at: str
    requests: int
    concurrency: int
    elapsed_seconds: float
    requests_per_second: float
    failures: int
    status_codes: dict[str, int]
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    threshold_ms: float
    passed: bool


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int((len(ordered) - 1) * fraction), len(ordered) - 1)]


def load_json(path: str | None, *, argument: str) -> dict[str, Any]:
    if not path:
        raise SystemExit(f"{argument} is required for this scenario")
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{argument} must contain a JSON object")
    return value


def unique_callback(payload: dict[str, Any], index: int) -> dict[str, Any]:
    candidate = deepcopy(payload)
    callback = candidate.get("Body", {}).get("stkCallback", {})
    suffix = f"load-{index}-{uuid.uuid4().hex[:10]}"
    callback["CheckoutRequestID"] = suffix
    callback["MerchantRequestID"] = f"MR-{suffix}"
    return candidate


async def run(args: argparse.Namespace) -> Result:
    if args.requests < 1 or args.concurrency < 1:
        raise SystemExit("--requests and --concurrency must be positive")
    if args.concurrency > 200:
        raise SystemExit("--concurrency is capped at 200")
    if args.scenario in WRITE_SCENARIOS and not args.allow_writes:
        raise SystemExit(f"{args.scenario} requires --allow-writes")
    if args.scenario == "concurrent-stk" and args.environment != "sandbox":
        raise SystemExit("concurrent-stk is restricted to --environment sandbox")
    if args.scenario == "concurrent-stk" and not args.token:
        raise SystemExit("concurrent-stk requires --token")
    if args.scenario != "ready" and args.scenario not in WRITE_SCENARIOS and not args.token:
        raise SystemExit("--token is required for authenticated read scenarios")

    callback_payload = (
        load_json(args.callback_payload, argument="--callback-payload")
        if args.scenario in {"callback-burst", "duplicate-callback"}
        else None
    )
    stk_payload = (
        load_json(args.stk_payload, argument="--stk-payload")
        if args.scenario == "concurrent-stk"
        else None
    )
    if args.scenario in {"callback-burst", "duplicate-callback"} and not args.merchant_id:
        raise SystemExit("--merchant-id is required for callback scenarios")

    semaphore = asyncio.Semaphore(args.concurrency)
    durations: list[float] = []
    statuses: dict[str, int] = {}
    failures = 0
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=httpx.Timeout(args.timeout_seconds),
    ) as client:

        async def one(index: int) -> None:
            nonlocal failures
            async with semaphore:
                request_headers = dict(headers)
                before = time.perf_counter()
                try:
                    if args.scenario == "ready":
                        response = await client.get("/ready")
                    elif args.scenario == "payments-read":
                        response = await client.get("/api/v1/payments", headers=request_headers)
                    elif args.scenario in {"callback-burst", "duplicate-callback"}:
                        body = (
                            unique_callback(callback_payload or {}, index)
                            if args.scenario == "callback-burst"
                            else callback_payload
                        )
                        response = await client.post(
                            f"/api/v1/callbacks/mpesa/{args.merchant_id}",
                            json=body,
                            headers={"X-Request-ID": f"load-callback-{uuid.uuid4()}"},
                        )
                    else:
                        body = deepcopy(stk_payload or {})
                        body["external_reference"] = (
                            f"{body.get('external_reference', 'load')}-{index}-{uuid.uuid4().hex[:8]}"
                        )[:120]
                        request_headers["Idempotency-Key"] = f"load-stk-{uuid.uuid4()}"
                        response = await client.post(
                            "/api/v1/payments/stk-push",
                            json=body,
                            headers=request_headers,
                        )
                    statuses[str(response.status_code)] = (
                        statuses.get(str(response.status_code), 0) + 1
                    )
                    if response.status_code not in {200, 201, 202}:
                        failures += 1
                except httpx.HTTPError:
                    statuses["transport_error"] = statuses.get("transport_error", 0) + 1
                    failures += 1
                finally:
                    durations.append(time.perf_counter() - before)

        await asyncio.gather(*(one(index) for index in range(args.requests)))

    elapsed = time.perf_counter() - started
    milliseconds = [duration * 1000 for duration in durations]
    p95_ms = percentile(milliseconds, 0.95)
    return Result(
        scenario=args.scenario,
        target=args.base_url,
        started_at=started_at,
        requests=len(milliseconds),
        concurrency=args.concurrency,
        elapsed_seconds=round(elapsed, 3),
        requests_per_second=round(len(milliseconds) / elapsed, 2),
        failures=failures,
        status_codes=statuses,
        mean_ms=round(statistics.mean(milliseconds), 2),
        p50_ms=round(percentile(milliseconds, 0.50), 2),
        p95_ms=round(p95_ms, 2),
        p99_ms=round(percentile(milliseconds, 0.99), 2),
        max_ms=round(max(milliseconds), 2),
        threshold_ms=args.max_p95_ms,
        passed=failures == 0 and p95_ms <= args.max_p95_ms,
    )


def write_report(result: Result, output_dir: str) -> tuple[Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = destination / f"{stamp}-{result.scenario}"
    json_path = stem.with_suffix(".json")
    markdown_path = stem.with_suffix(".md")
    json_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(
        "\n".join(
            [
                f"# LynxPay performance: {result.scenario}",
                "",
                f"- Target: `{result.target}`",
                f"- Started: `{result.started_at}`",
                f"- Requests: `{result.requests}` at concurrency `{result.concurrency}`",
                f"- Throughput: `{result.requests_per_second} req/s`",
                f"- Statuses: `{json.dumps(result.status_codes, sort_keys=True)}`",
                f"- Failures: `{result.failures}`",
                (
                    f"- Latency: mean `{result.mean_ms} ms`, p50 `{result.p50_ms} ms`, "
                    f"p95 `{result.p95_ms} ms`, p99 `{result.p99_ms} ms`, "
                    f"max `{result.max_ms} ms`"
                ),
                f"- Gate: p95 <= `{result.threshold_ms} ms`",
                f"- Result: `{'PASS' if result.passed else 'FAIL'}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=["ready", "payments-read", *sorted(WRITE_SCENARIOS)],
        default="payments-read",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token")
    parser.add_argument("--merchant-id")
    parser.add_argument("--callback-payload")
    parser.add_argument("--stk-payload")
    parser.add_argument("--environment", choices=["sandbox", "production"], default="sandbox")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    parser.add_argument("--max-p95-ms", type=float, default=1000)
    parser.add_argument("--output-dir", default="artifacts/performance")
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    json_path, markdown_path = write_report(result, args.output_dir)
    print(
        f"scenario={result.scenario} requests={result.requests} failures={result.failures} "
        f"rps={result.requests_per_second} p95_ms={result.p95_ms} "
        f"result={'PASS' if result.passed else 'FAIL'}"
    )
    print(f"reports={json_path},{markdown_path}")
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

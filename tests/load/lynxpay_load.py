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
DEFAULT_P95_THRESHOLDS_MS = {
    "ready": 500.0,
    "payments-read": 750.0,
    "callback-burst": 750.0,
    "duplicate-callback": 750.0,
    "concurrent-stk": 2500.0,
}


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
    assertions: dict[str, bool]
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


def callback_checkout_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("Body", {}).get("stkCallback", {}).get("CheckoutRequestID")
    return str(value) if value else None


async def callback_evidence(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    merchant_id: str,
    checkout_ids: set[str],
    expected_count: int,
    received_after: datetime,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    before: str | None = None
    for _ in range(20):
        params: dict[str, str | int] = {"merchant_id": merchant_id, "limit": 500}
        if before:
            params["before"] = before
        response = await client.get("/api/v1/callbacks", params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("items", [])
        records.extend(
            row
            for row in rows
            if row.get("checkout_request_id") in checkout_ids
            and row.get("received_at")
            and datetime.fromisoformat(str(row["received_at"])) >= received_after
        )
        if (
            checkout_ids.issubset({str(row.get("checkout_request_id")) for row in records})
            and len(records) >= expected_count
        ):
            break
        before = payload.get("next_before")
        if not before:
            break
    return records


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
    if (
        args.scenario in {"callback-burst", "duplicate-callback"}
        and not args.token
        and not args.skip_correctness_assertions
    ):
        raise SystemExit(
            "callback correctness assertions require --token; "
            "use --skip-correctness-assertions only for latency diagnostics"
        )
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
    checkout_ids: set[str] = set()
    payment_ids: list[str] = []
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    started_datetime = datetime.now(timezone.utc)
    started_at = started_datetime.isoformat()
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
                        checkout_id = callback_checkout_id(body or {})
                        if checkout_id:
                            checkout_ids.add(checkout_id)
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
                        if response.status_code in {200, 201, 202}:
                            response_payload = response.json()
                            if response_payload.get("id"):
                                payment_ids.append(str(response_payload["id"]))
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
        assertions: dict[str, bool] = {}
        if not args.skip_correctness_assertions and args.scenario in {
            "callback-burst",
            "duplicate-callback",
        }:
            records = await callback_evidence(
                client,
                headers=headers,
                merchant_id=args.merchant_id,
                checkout_ids=checkout_ids,
                expected_count=args.requests,
                received_after=started_datetime,
            )
            assertions["all_callback_evidence_preserved"] = len(records) == args.requests
            if args.scenario == "duplicate-callback":
                duplicate_count = sum(
                    row.get("processing_status") == "duplicate"
                    or bool(row.get("duplicate_of_callback_id"))
                    for row in records
                )
                assertions["duplicate_callbacks_classified"] = duplicate_count >= args.requests - 1
        elif not args.skip_correctness_assertions and args.scenario == "concurrent-stk":
            assertions["all_stk_payments_returned"] = len(payment_ids) == args.requests
            duplicate_transitions = 0
            for payment_id in payment_ids:
                response = await client.get(
                    f"/api/v1/payments/{payment_id}/timeline",
                    headers=headers,
                )
                if response.status_code != 200:
                    duplicate_transitions += 1
                    continue
                signatures: dict[tuple[str, str, str], int] = {}
                for row in response.json().get("ledger", []):
                    signature = (
                        str(row.get("event_type")),
                        str(row.get("status_from")),
                        str(row.get("status_to")),
                    )
                    signatures[signature] = signatures.get(signature, 0) + 1
                duplicate_transitions += sum(
                    count - 1 for count in signatures.values() if count > 1
                )
            assertions["no_duplicate_ledger_transitions"] = duplicate_transitions == 0

    elapsed = time.perf_counter() - started
    milliseconds = [duration * 1000 for duration in durations]
    p95_ms = percentile(milliseconds, 0.95)
    threshold_ms = args.max_p95_ms or DEFAULT_P95_THRESHOLDS_MS[args.scenario]
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
        threshold_ms=threshold_ms,
        assertions=assertions,
        passed=(failures == 0 and p95_ms <= threshold_ms and all(assertions.values())),
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
                f"- Correctness assertions: `{json.dumps(result.assertions, sort_keys=True)}`",
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
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--output-dir", default="artifacts/performance")
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--skip-correctness-assertions", action="store_true")
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

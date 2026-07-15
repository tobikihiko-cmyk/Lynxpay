"""Bounded read-only LynxPay load probe; never initiates payments."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def run(
    base_url: str, token: str, requests: int, concurrency: int
) -> tuple[list[float], int]:
    semaphore = asyncio.Semaphore(concurrency)
    durations: list[float] = []
    failures = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:

        async def one() -> None:
            nonlocal failures
            async with semaphore:
                started = time.perf_counter()
                response = await client.get(
                    "/api/v1/payments", headers={"Authorization": f"Bearer {token}"}
                )
                durations.append(time.perf_counter() - started)
                if response.status_code != 200:
                    failures += 1

        await asyncio.gather(*(one() for _ in range(requests)))
    return durations, failures


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only LynxPay load probe")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--max-p95-ms", type=float, default=1000)
    args = parser.parse_args()
    started = time.perf_counter()
    durations, failures = asyncio.run(
        run(args.base_url, args.token, args.requests, args.concurrency)
    )
    elapsed = time.perf_counter() - started
    p95_ms = percentile(durations, 0.95) * 1000
    print(
        f"requests={len(durations)} failures={failures} rps={len(durations) / elapsed:.1f} "
        f"mean_ms={statistics.mean(durations) * 1000:.1f} p95_ms={p95_ms:.1f}"
    )
    if failures or p95_ms > args.max_p95_ms:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

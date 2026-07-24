"""Run guarded Docker outage drills and write recovery evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import httpx


def compose(
    args: argparse.Namespace, *command: str, check: bool = True
) -> subprocess.CompletedProcess:
    invocation = ["docker", "compose"]
    for compose_file in args.compose_file:
        invocation.extend(["-f", compose_file])
    invocation.extend(command)
    return subprocess.run(invocation, check=check, capture_output=True, text=True)


def wait_for_status(
    url: str,
    expected: int,
    *,
    headers: dict[str, str] | None,
    timeout_seconds: float,
) -> tuple[bool, float, list[int | str]]:
    started = time.monotonic()
    observed: list[int | str] = []
    while time.monotonic() - started < timeout_seconds:
        try:
            response = httpx.get(url, headers=headers, timeout=3)
            observed.append(response.status_code)
            if response.status_code == expected:
                return True, time.monotonic() - started, observed
        except httpx.HTTPError as exc:
            observed.append(type(exc).__name__)
        time.sleep(0.5)
    return False, time.monotonic() - started, observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["redis-outage", "worker-crash"])
    parser.add_argument(
        "--compose-file",
        action="append",
        default=[],
        help="Repeat for each Compose file; defaults to docker-compose.yml",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token")
    parser.add_argument("--recovery-seconds", type=float, default=60)
    parser.add_argument("--output-dir", default="artifacts/failure")
    parser.add_argument("--allow-disruption", action="store_true")
    args = parser.parse_args()
    if not args.allow_disruption:
        raise SystemExit("outage drills require --allow-disruption")
    if not args.compose_file:
        args.compose_file = ["docker-compose.yml"]

    started_at = datetime.now(timezone.utc).isoformat()
    evidence: dict = {
        "scenario": args.scenario,
        "started_at": started_at,
        "compose_files": args.compose_file,
        "passed": False,
    }
    service = "redis" if args.scenario == "redis-outage" else "worker"
    recovered = False
    try:
        compose(args, "stop", service)
        if args.scenario == "redis-outage":
            if not args.token:
                raise SystemExit("--token is required for the authenticated Redis outage probe")
            headers = {"Authorization": f"Bearer {args.token}"}
            failed_closed, fail_closed_seconds, fail_observed = wait_for_status(
                f"{args.base_url.rstrip('/')}/api/v1/payments",
                503,
                headers=headers,
                timeout_seconds=15,
            )
            evidence.update(
                {
                    "failed_closed": failed_closed,
                    "fail_closed_seconds": round(fail_closed_seconds, 3),
                    "outage_statuses": fail_observed,
                }
            )
        else:
            time.sleep(2)
            stopped = compose(args, "ps", "--status", "running", "--services").stdout.splitlines()
            evidence["worker_stopped"] = "worker" not in stopped

        compose(args, "start", service)
        if args.scenario == "redis-outage":
            recovered, recovery_seconds, recovery_observed = wait_for_status(
                f"{args.base_url.rstrip('/')}/api/v1/payments",
                200,
                headers={"Authorization": f"Bearer {args.token}"},
                timeout_seconds=args.recovery_seconds,
            )
            evidence.update(
                {
                    "recovered": recovered,
                    "recovery_seconds": round(recovery_seconds, 3),
                    "recovery_statuses": recovery_observed,
                }
            )
            evidence["passed"] = bool(evidence.get("failed_closed") and recovered)
        else:
            deadline = time.monotonic() + args.recovery_seconds
            health_results: list[int] = []
            while time.monotonic() < deadline:
                result = compose(
                    args,
                    "exec",
                    "-T",
                    "worker",
                    "python",
                    "-m",
                    "app.worker",
                    "--mode",
                    "all",
                    "--health-check",
                    check=False,
                )
                health_results.append(result.returncode)
                if result.returncode == 0:
                    recovered = True
                    break
                time.sleep(1)
            evidence["worker_health_exit_codes"] = health_results
            evidence["recovered"] = recovered
            evidence["passed"] = bool(evidence.get("worker_stopped") and recovered)
    finally:
        if not recovered:
            compose(args, "start", service, check=False)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = output_dir / f"{stamp}-{args.scenario}.json"
    report.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"scenario={args.scenario} result={'PASS' if evidence['passed'] else 'FAIL'}")
    print(f"report={report}")
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

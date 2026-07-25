"""Fail a release when persistence, queue-age, or lease invariants are violated."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from sqlalchemy import create_engine, text


@dataclass
class GateReport:
    checked_at: str
    since: str
    database: str
    thresholds: dict[str, float | int]
    observed: dict[str, float | int]
    assertions: dict[str, bool]
    passed: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seconds_old(value: datetime | None, now: datetime) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max((now - value).total_seconds(), 0.0)


def scalar(connection, statement: str, **parameters):
    return connection.execute(text(statement), parameters).scalar()


def observations(engine, since: datetime) -> dict[str, float | int]:
    now = utcnow()
    with engine.connect() as connection:
        duplicate_terminal_ledgers = scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT payment_id, event_type, status_from, status_to
                FROM lynxpay_payment_ledger
                WHERE created_at >= :since
                  AND event_type IN ('payment.success', 'payment.reversed')
                GROUP BY payment_id, event_type, status_from, status_to
                HAVING COUNT(*) > 1
            ) duplicates
            """,
            since=since,
        )
        callbacks_received = scalar(
            connection,
            "SELECT COUNT(*) FROM lynxpay_mpesa_callbacks WHERE received_at >= :since",
            since=since,
        )
        callbacks_without_raw_evidence = scalar(
            connection,
            """
            SELECT COUNT(*) FROM lynxpay_mpesa_callbacks
            WHERE received_at >= :since
              AND (raw_body IS NULL OR raw_body = '' OR raw_payload IS NULL)
            """,
            since=since,
        )
        oldest_reconciliation = scalar(
            connection,
            """
            SELECT MIN(next_reconciliation_at)
            FROM lynxpay_payments
            WHERE status IN ('stk_sent', 'unknown')
              AND next_reconciliation_at IS NOT NULL
              AND next_reconciliation_at <= :now
            """,
            now=now,
        )
        oldest_webhook = scalar(
            connection,
            """
            SELECT MIN(COALESCE(next_retry_at, created_at))
            FROM lynxpay_webhook_deliveries
            WHERE status IN ('queued', 'retrying', 'delivering')
              AND COALESCE(next_retry_at, created_at) <= :now
            """,
            now=now,
        )
        due_webhooks = scalar(
            connection,
            """
            SELECT COUNT(*) FROM lynxpay_webhook_deliveries
            WHERE status IN ('queued', 'retrying', 'delivering')
              AND COALESCE(next_retry_at, created_at) <= :now
            """,
            now=now,
        )
        stale_webhook_leases = scalar(
            connection,
            """
            SELECT COUNT(*) FROM lynxpay_webhook_deliveries
            WHERE lease_owner IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= :now
            """,
            now=now,
        )
        stale_reconciliation_leases = scalar(
            connection,
            """
            SELECT COUNT(*) FROM lynxpay_payments
            WHERE reconciliation_lease_owner IS NOT NULL
              AND reconciliation_lease_expires_at IS NOT NULL
              AND reconciliation_lease_expires_at <= :now
            """,
            now=now,
        )
    return {
        "duplicate_terminal_ledger_groups": int(duplicate_terminal_ledgers or 0),
        "callbacks_received": int(callbacks_received or 0),
        "callbacks_without_raw_evidence": int(callbacks_without_raw_evidence or 0),
        "oldest_reconciliation_age_seconds": round(seconds_old(oldest_reconciliation, now), 3),
        "oldest_webhook_age_seconds": round(seconds_old(oldest_webhook, now), 3),
        "due_webhooks": int(due_webhooks or 0),
        "stale_webhook_leases": int(stale_webhook_leases or 0),
        "stale_reconciliation_leases": int(stale_reconciliation_leases or 0),
    }


def write_report(report: GateReport, output_dir: str) -> tuple[Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = destination / f"{stamp}-database-gates"
    json_path = stem.with_suffix(".json")
    markdown_path = stem.with_suffix(".md")
    json_path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(
        "\n".join(
            [
                "# LynxPay database release gates",
                "",
                f"- Checked: `{report.checked_at}`",
                f"- Since: `{report.since}`",
                f"- Database: `{report.database}`",
                f"- Thresholds: `{json.dumps(report.thresholds, sort_keys=True)}`",
                f"- Observed: `{json.dumps(report.observed, sort_keys=True)}`",
                f"- Assertions: `{json.dumps(report.assertions, sort_keys=True)}`",
                f"- Result: `{'PASS' if report.passed else 'FAIL'}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_ADMIN_URL") or os.getenv("DATABASE_URL"),
    )
    parser.add_argument("--since", required=True, help="UTC ISO timestamp before the test run")
    parser.add_argument("--expected-callbacks", type=int, default=0)
    parser.add_argument("--max-queue-age-seconds", type=float, default=120)
    parser.add_argument("--webhook-recovery-seconds", type=float, default=300)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--output-dir", default="artifacts/performance")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or DATABASE_ADMIN_URL is required")
    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    engine = create_engine(args.database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise SystemExit("release database gates require PostgreSQL")

    deadline = time.monotonic() + args.webhook_recovery_seconds
    observed = observations(engine, since)
    while observed["due_webhooks"] and time.monotonic() < deadline:
        time.sleep(args.poll_seconds)
        observed = observations(engine, since)

    assertions = {
        "no_duplicate_terminal_ledger_entries": (observed["duplicate_terminal_ledger_groups"] == 0),
        "all_expected_callbacks_retained": (
            observed["callbacks_received"] >= args.expected_callbacks
        ),
        "no_lost_callback_raw_evidence": (observed["callbacks_without_raw_evidence"] == 0),
        "reconciliation_queue_age_within_threshold": (
            observed["oldest_reconciliation_age_seconds"] <= args.max_queue_age_seconds
        ),
        "webhook_queue_age_within_threshold": (
            observed["oldest_webhook_age_seconds"] <= args.max_queue_age_seconds
        ),
        "webhook_backlog_recovered": observed["due_webhooks"] == 0,
        "no_stale_webhook_leases": observed["stale_webhook_leases"] == 0,
        "no_stale_reconciliation_leases": (observed["stale_reconciliation_leases"] == 0),
    }
    report = GateReport(
        checked_at=utcnow().isoformat(),
        since=since.isoformat(),
        database=engine.url.render_as_string(hide_password=True),
        thresholds={
            "expected_callbacks": args.expected_callbacks,
            "max_queue_age_seconds": args.max_queue_age_seconds,
            "webhook_recovery_seconds": args.webhook_recovery_seconds,
        },
        observed=observed,
        assertions=assertions,
        passed=all(assertions.values()),
    )
    json_path, markdown_path = write_report(report, args.output_dir)
    print(json_path)
    print(markdown_path)
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

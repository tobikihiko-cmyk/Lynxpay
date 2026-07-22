"""LynxPay background worker entry point."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import signal
import socket
import sys
import uuid

from app.core.config import settings
from app.core.datetime_utils import ensure_utc_datetime
from app.database import WorkerSessionLocal, worker_engine
from app.database_roles import validate_runtime_database_role
from app.email_delivery import claim_emails, deliver_claimed_email
from app.maintenance import abandon_stale_stk_submissions
from app.models import WorkerHeartbeat
from app.reconciliation import claim_reconciliations, reconcile_payment
from app.service import utcnow
from app.webhooks import claim_deliveries, deliver_claimed

WORKER_MODES = {"all", "webhooks", "reconciliation", "email", "maintenance"}


async def _drain_bounded_queue(
    worker_id: str,
    limit: int,
    claim,
    process,
) -> int:
    """Claim one network-backed item at a time so queued leases stay fresh.

    Provider, webhook, and SMTP calls have bounded timeouts shorter than the
    lease. Claiming a whole batch before the first call would nevertheless let
    later leases age while they wait behind earlier work.
    """

    processed = 0
    for _ in range(max(limit, 0)):
        with WorkerSessionLocal() as db:
            claimed_ids = claim(db, worker_id, 1)
        if not claimed_ids:
            break
        with WorkerSessionLocal() as db:
            await process(db, claimed_ids[0], worker_id)
        processed += 1
    return processed


async def _reconcile_claimed(db, payment_id: str, worker_id: str):
    return await reconcile_payment(db, payment_id, worker_id=worker_id)


def worker_is_healthy(mode: str, hostname: str | None = None) -> bool:
    """Report whether this container has a recent heartbeat for its queue."""

    expected_hostname = hostname or socket.gethostname()
    with WorkerSessionLocal() as db:
        heartbeats = (
            db.query(WorkerHeartbeat)
            .filter(WorkerHeartbeat.hostname == expected_hostname)
            .order_by(WorkerHeartbeat.last_seen_at.desc())
            .limit(20)
            .all()
        )
    now = utcnow()
    return any(
        (heartbeat.metadata_json or {}).get("mode") == mode
        and (now - ensure_utc_datetime(heartbeat.last_seen_at)).total_seconds()
        <= settings.WORKER_HEARTBEAT_MAX_AGE_SECONDS
        for heartbeat in heartbeats
    )


async def run_once(worker_id: str, limit: int, mode: str = "all") -> int:
    if mode not in WORKER_MODES:
        raise ValueError(f"Unknown worker mode: {mode}")
    hostname = socket.gethostname()
    with WorkerSessionLocal() as db:
        heartbeat = db.query(WorkerHeartbeat).filter(WorkerHeartbeat.worker_id == worker_id).first()
        if not heartbeat:
            heartbeat = WorkerHeartbeat(
                worker_id=worker_id,
                hostname=hostname,
                last_seen_at=utcnow(),
                processed_total=0,
            )
            db.add(heartbeat)
        else:
            heartbeat.last_seen_at = utcnow()
        db.commit()
    deliveries_processed = 0
    reconciliations_processed = 0
    emails_processed = 0
    maintenance_ids: list[str] = []
    if mode in {"all", "webhooks"}:
        deliveries_processed = await _drain_bounded_queue(
            worker_id, limit, claim_deliveries, deliver_claimed
        )
    if mode in {"all", "reconciliation"}:
        reconciliations_processed = await _drain_bounded_queue(
            worker_id, limit, claim_reconciliations, _reconcile_claimed
        )
    if mode in {"all", "email"}:
        emails_processed = await _drain_bounded_queue(
            worker_id, limit, claim_emails, deliver_claimed_email
        )
    if mode in {"all", "maintenance"}:
        with WorkerSessionLocal() as db:
            maintenance_ids = abandon_stale_stk_submissions(db, limit)
    processed = (
        deliveries_processed + reconciliations_processed + emails_processed + len(maintenance_ids)
    )
    with WorkerSessionLocal() as db:
        heartbeat = db.query(WorkerHeartbeat).filter(WorkerHeartbeat.worker_id == worker_id).one()
        heartbeat.last_seen_at = utcnow()
        heartbeat.processed_total += processed
        heartbeat.metadata_json = {
            "webhooks": deliveries_processed,
            "reconciliations": reconciliations_processed,
            "emails": emails_processed,
            "maintenance": len(maintenance_ids),
            "mode": mode,
        }
        db.commit()
    return processed


async def run_forever(
    worker_id: str,
    limit: int,
    poll_seconds: float,
    mode: str = "all",
    stop_event: asyncio.Event | None = None,
) -> None:
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        processed = await run_once(worker_id, limit, mode)
        if not processed:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)


async def _run_until_stopped(worker_id: str, limit: int, poll_seconds: float, mode: str) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):  # pragma: no cover - non-POSIX event loops
            loop.add_signal_handler(signum, stop_event.set)
    await run_forever(worker_id, limit, poll_seconds, mode, stop_event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver queued LynxPay webhooks")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--mode", choices=sorted(WORKER_MODES), default="all")
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args()
    settings.validate_runtime()
    if settings.is_production and settings.PROCESS_TYPE.strip().lower() != "worker":
        raise RuntimeError("Background workers require PROCESS_TYPE=worker")
    validate_runtime_database_role(worker_engine, f"{args.mode} worker")
    if args.health_check:
        if not worker_is_healthy(args.mode):
            sys.exit(1)
        return
    worker_id = f"{socket.gethostname()}-{uuid.uuid4()}"
    if args.once:
        asyncio.run(run_once(worker_id, args.limit, args.mode))
    else:
        asyncio.run(_run_until_stopped(worker_id, args.limit, args.poll_seconds, args.mode))


if __name__ == "__main__":
    main()

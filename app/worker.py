"""LynxPay background worker entry point."""

from __future__ import annotations

import argparse
import asyncio
import socket
import uuid

from app.database import WorkerSessionLocal
from app.email_delivery import claim_emails, deliver_claimed_email
from app.models import WorkerHeartbeat
from app.reconciliation import claim_reconciliations, reconcile_payment
from app.service import utcnow
from app.webhooks import claim_deliveries, deliver_claimed


async def run_once(worker_id: str, limit: int) -> int:
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
    with WorkerSessionLocal() as db:
        delivery_ids = claim_deliveries(db, worker_id, limit)
    for delivery_id in delivery_ids:
        with WorkerSessionLocal() as db:
            await deliver_claimed(db, delivery_id, worker_id)
    with WorkerSessionLocal() as db:
        payment_ids = claim_reconciliations(db, worker_id, limit)
    for payment_id in payment_ids:
        with WorkerSessionLocal() as db:
            await reconcile_payment(db, payment_id, worker_id=worker_id)
    with WorkerSessionLocal() as db:
        email_ids = claim_emails(db, worker_id, limit)
    for email_id in email_ids:
        with WorkerSessionLocal() as db:
            await deliver_claimed_email(db, email_id, worker_id)
    processed = len(delivery_ids) + len(payment_ids) + len(email_ids)
    with WorkerSessionLocal() as db:
        heartbeat = db.query(WorkerHeartbeat).filter(WorkerHeartbeat.worker_id == worker_id).one()
        heartbeat.last_seen_at = utcnow()
        heartbeat.processed_total += processed
        heartbeat.metadata_json = {
            "webhooks": len(delivery_ids),
            "reconciliations": len(payment_ids),
            "emails": len(email_ids),
        }
        db.commit()
    return processed


async def run_forever(worker_id: str, limit: int, poll_seconds: float) -> None:
    while True:
        processed = await run_once(worker_id, limit)
        if not processed:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver queued LynxPay webhooks")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    worker_id = f"{socket.gethostname()}-{uuid.uuid4()}"
    if args.once:
        asyncio.run(run_once(worker_id, args.limit))
    else:
        asyncio.run(run_forever(worker_id, args.limit, args.poll_seconds))


if __name__ == "__main__":
    main()

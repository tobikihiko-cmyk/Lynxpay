# LynxPay escalation policy

## Roles

- **Incident commander:** owns severity, timeline, decisions, and closure.
- **Payment operations lead:** protects payment state and provider evidence.
- **Infrastructure lead:** owns Render, PostgreSQL, Redis, workers, and rollback.
- **Security lead:** owns credential exposure, suspicious access, and forensics.
- **Merchant communications lead:** gives affected merchants factual updates.

The on-call engineer may fill more than one role initially, but a page-level
payment or security incident requires a second responder.

## Severity

| Severity | Examples | Acknowledge | Update cadence |
| --- | --- | --- | --- |
| SEV-1 | False success, duplicate customer charge, widespread callback loss, credential compromise, unavailable collection across merchants | 5 minutes | 15 minutes |
| SEV-2 | One merchant materially impaired, unknown/reconciliation backlog over threshold, failed reversal, worker mode down, database pool saturation | 15 minutes | 30 minutes |
| SEV-3 | Degraded latency, isolated webhook/email dead letters, non-urgent merchant-specific failure trend | 4 business hours | Daily until owned |

SEV-1 and SEV-2 require an incident channel, incident commander, evidence log,
and explicit closure. Security incidents follow the shorter applicable timing.

## Escalation

1. The alert receiver acknowledges and checks the linked runbook.
2. Page a second responder if a payment-state alert is not explained in five minutes.
3. Page the infrastructure lead for Redis, worker, pool, deploy, or database symptoms.
4. Page payment operations for unknown payments, callback contradictions, duplicate receipts, or reversals.
5. Page security immediately for leaked credentials, unauthorized tenant access, or evidence tampering.
6. Contact Safaricom through the approved support path only after collecting merchant, operation, timestamp, response code, and correlation evidence.
7. Notify affected merchants with known facts, impact, workarounds, and next update time. Do not speculate or disclose another merchant's data.

## Authority

The incident commander may:

- pause new payment initiation while preserving callback intake and reconciliation;
- suspend one merchant or webhook endpoint;
- scale within tested limits;
- roll back to a known-good application commit;
- rotate exposed application/API/Daraja credentials through approved procedures.

The incident commander may not:

- manually mark a payment successful without provider evidence;
- delete or edit ledger, audit, callback, or attempt evidence;
- automatically retry an uncertain STK request;
- downgrade the production database without an approved data-impact plan;
- approve their own reversal request or bypass MFA/maker-checker controls.

## Closure

Close only after payment safety is established, queues recover, alerts clear,
merchant communications are complete, and evidence is retained. SEV-1 and
SEV-2 incidents require a blameless review with owned actions and due dates.


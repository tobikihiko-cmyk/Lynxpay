# LynxPay incident response

## First actions

1. Assign an incident commander and record UTC/Kenya timestamps.
2. Preserve audit, callback, webhook-attempt, status-check, API, PostgreSQL, Redis, and trace evidence.
3. Do not mark payments successful, re-initiate STK requests, rotate/delete credentials, or replay webhooks merely to clear an alert.
4. Establish affected organizations, merchants, CheckoutRequestIDs, time range, and deployment version.
5. Communicate facts and uncertainty; never expose customer phones, API keys, Daraja credentials, or raw tokens.

## API errors

- Compare error rate by route/status and correlate trace IDs with database/Redis health.
- If Daraja is degraded, keep accepted payments pending/unknown and let reconciliation verify them.
- Roll back only a known bad application release. Database downgrades require a separately approved data-impact review.

## Latency

- Check PostgreSQL locks, connection saturation, slow queries, Redis latency, worker backlog, and Daraja latency independently.
- Scale stateless API/worker replicas only after confirming the database and downstream limits can support it.

## Rate limiting

- Confirm whether traffic is abusive or a legitimate merchant burst.
- Never disable global protection during an active attack. Apply a time-bounded, merchant-specific limit change with audit evidence.
- Redis failure is fail-closed in production; restore Redis quorum/persistence rather than bypassing it.

## Payment or callback anomaly

- Preserve raw payloads and lock affected records from manual edits.
- Compare amount, phone, receipt, merchant, callback, reconciliation response, and ledger.
- Duplicate receipts or contradictory states require an explicit audited correction workflow; do not mutate ledger history.

## Credential exposure

- Disable the affected credential/API key, notify the merchant, rotate at the provider, and record exact scope.
- Rotate envelope/KMS keys only if wrapping keys may be affected. Keep old KMS versions until all ciphertext is re-encrypted and verified.

## Closure

- Confirm payment safety, backlog recovery, alerts cleared, and no unverified success transitions.
- Produce a blameless timeline, root cause, customer impact, evidence links, and owned corrective actions.

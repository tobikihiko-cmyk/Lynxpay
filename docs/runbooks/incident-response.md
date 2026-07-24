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

## Callback rate limited

1. Split the alert by verified and unverified callback class, merchant route, and trusted source IP.
2. Confirm trusted-proxy resolution and Safaricom allowlist health before changing any budget.
3. Preserve rejected-request metrics and inspect callback backlog/unmatched evidence. Never synthesize success.
4. Increase only the verified-source per-merchant budget under a time-bounded incident change; keep the unverified budget constrained.

## Investigate a missing customer payment

1. Search by merchant, CheckoutRequestID, receipt, external reference, amount, and normalized phone.
2. Compare the durable STK attempt, raw callback evidence, payment ledger, and status-check history.
3. If the attempt is `submitting` past its limit, let maintenance mark it `abandoned`/`unknown`; do not issue another STK request automatically.
4. Reconcile an eligible payment through Daraja. Only verified provider evidence can establish success.

## Link an unmatched callback

1. Verify the platform operator has recent MFA and record the support case in the link reason.
2. Match merchant, checkout/merchant request evidence, exact amount, normalized phone, and receipt uniqueness.
3. Use the platform-admin link action once. Do not edit callback or ledger rows directly.
4. Confirm the new payment state, immutable audit event, ledger event, and queued webhook.

## Replay or disable a webhook

1. Inspect endpoint DNS/IP validation, last response, bounded body, attempts, and delivery event ID.
2. Replay by creating a new delivery; never rewrite an old delivery or attempt.
3. Pause a harmful endpoint to stop retry pressure. Re-enable only after ownership and endpoint health are confirmed.

## Retry a failed STK

Only an explicit merchant/user action may create a new attempt, and only when prior provider evidence proves the original was not sent or definitively failed. Preserve the same payment identity and audit the retry reason. Never retry `submitting`, `uncertain`, `stk_sent`, or `unknown` automatically.

## Approve, reject, or suspend a production merchant

Require an independent platform administrator with recent MFA. Approval requires verified owner, consent versions, tested production credentials, and callback-confirmed KES 1 evidence. Suspension stops new initiation but preserves all historical evidence and in-flight reconciliation.

## Rotate Daraja credentials

Disable initiation during the change, store only the newly encrypted bundle, test OAuth in the correct environment, audit the actor, and retain no plaintext or log output. Reactivation follows the normal verification/approval gate.

## Worker heartbeat missing

Check the affected worker mode, lease expiry, PostgreSQL connectivity, process termination, and deployment health. Restart safely; expired leases are recoverable. Do not run the combined worker in production to mask a failed mode.

## Database pool saturation

Compare checked-out connections, long transactions, query latency, PostgreSQL `max_connections`, and per-process replica counts. Stop runaway deploy scaling before raising pool size. Reconciliation must not hold a connection or lock during provider network calls.

## Daraja errors

Separate OAuth, STK initiation, and status-query latency/errors by sandbox/production. Confirm Safaricom status and merchant credential validity. Treat transport timeouts and malformed acceptances as uncertain, not failed, and never issue a second STK request automatically.

## Merchant-specific failures

Compare the merchant's final outcomes, Safaricom result codes, credential
environment, shortcode, callback arrival, and customer cancellation rate with
the platform baseline. Suspend only that merchant's initiation when the fault is
isolated. Preserve callback and reconciliation processing for in-flight
payments, and never expose another merchant's rates or identifiers.

## Reversal needs review

1. Confirm request, independent approver, recent MFA, reason, payment success evidence, amount, receipt, and initiator credentials.
2. Compare submission response, result/timeout callback, status, correlation ID, audit events, and ledger.
3. Do not submit a second reversal while the first is accepted, uncertain, or awaiting callback.
4. A successful provider callback is required before changing the payment to `reversed` and reopening its invoice.
5. Escalate unknown or timed-out reversals to Safaricom with redacted evidence; record the support reference without mutating history.

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

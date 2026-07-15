# LynxPay Version 2 technical assessment

Date: 2026-07-16  
Target: a controlled production pilot for the first 100 businesses  
Boundary: Safaricom M-PESA Daraja infrastructure only; LynxPay does not aggregate, hold, or settle merchant funds.

## 1. Existing architecture summary

LynxPay is already a standalone FastAPI service with PostgreSQL/SQLAlchemy persistence, Alembic migrations, a database-leased worker, Redis-backed rate limiting, native identity, an Nginx-served static operations console, and deployment/test containers. The synchronous API persists payment and callback evidence; a separate worker handles webhook delivery, reconciliation, and email outbox work.

The payment plane is tenant-scoped in application queries and protected by PostgreSQL row-level security. Credentials use versioned envelope encryption with local-keyring and AWS KMS providers. API keys, refresh tokens, reset tokens, invitation tokens, and recovery codes are stored as hashes. Payment ledger and audit records are append-only in both SQLAlchemy and PostgreSQL.

Daraja remains a concrete integration rather than a generic provider framework. That is correct for the 100-business pilot and should remain unchanged in Version 2.

## 2. Current frontend state

### Working

- The `dashboard/` console is dependency-free, Daraja-specific, responsive, keyboard-aware, reduced-motion aware, and protected by restrictive Nginx browser headers.
- It supports registration, sign-in, recovery, MFA input, the six-step onboarding sequence, organization profile, PayBill/Till/store-number setup, credential verification, KES 1 verification, activation, STK Push, payments, and callback logs.
- Its information hierarchy correctly distinguishes STK acceptance from verified payment success and exposes sandbox/live context.

### Missing or risky

- It is still a single-file JavaScript application without typed API contracts, route-level modules, component tests, error boundaries, or a maintainable design-system boundary.
- Access and refresh tokens are stored in `sessionStorage`; this is not an acceptable final production administrative boundary.
- There are no payment-detail/timeline, reconciliation, API-key management, webhook management, audit-log, team/session, or platform-admin views.
- Payment lists have no URL-persisted filters, cursor pagination, search, or detail drill-down.
- The console has static accessibility contract tests but no browser interaction suite.

### Version 2 decision

Keep `dashboard/` as the internal/operational console during migration. Build `apps/merchant-dashboard/` as the merchant-facing application with Next.js, TypeScript, a same-origin backend-for-frontend session boundary, typed forms, route modules, and browser tests. Do not delete the internal console until the new application covers recovery and operational workflows.

## 3. Current backend state

### Working

- STK initiation creates a durable `Payment`, an append-only ledger entry, an audit record, and `PaymentAttempt` before outbound network I/O.
- Known-not-sent, provider rejection, uncertain transport, accepted STK, callback success/failure, timeout, and reconciliation outcomes are differentiated.
- Idempotency request fingerprints, hashed idempotency keys for newly created payments, unique external references, unique CheckoutRequestIDs, and merchant-scoped unique receipts are present.
- Callbacks are body-limited, raw-first persisted, source-checked, classified, tenant-bound, row-locked, evidence-validated, duplicate-aware, and conflict-aware.
- Invalid success evidence does not prevent a later valid callback.
- Reconciliation uses database leasing and `SKIP LOCKED`, records every status query, and does not overwrite terminal success.
- Webhooks are signed, leased, retried with exponential backoff/jitter, dead-lettered, replayable, response-limited, redirect-disabled, and protected against DNS/private-address SSRF.
- Native identity includes password reset, TOTP MFA, recovery codes, refresh-token rotation/reuse detection, session revocation, encrypted email outbox, and team invitations.
- Structured low-cardinality metrics, tracing hooks, health/readiness checks, and distributed rate limiting exist.

### Missing or risky

- There is no payment retry endpoint. `failed` and `timeout` are terminal in the current state machine, and retry policy/evidence is not modeled.
- Payment evidence is implicit in ledger details. `success_source`, `receipt_status`, `review_status`, `review_reason`, and `provider_acceptance_state` are absent.
- Legacy raw idempotency keys are still queried for compatibility. A migration must normalize or retire that fallback before claiming the column is hash-only.
- Reconciliation uses one fixed interval and attempt count; it lacks elapsed-time backoff bands and an explicit manual-review queue.
- Email verification is absent. Registration immediately activates the user and organization.
- Merchant `verified -> active` is performed by the merchant organization owner. There is no independent LynxPay platform-admin identity or production approval record.
- Terms/privacy acceptance and versioned consent evidence are absent.
- `webhooks:read` is absent; reads currently require `webhooks:write`. Endpoint deletion/archive, URL updates, secret rotation, test delivery, and attempt-detail APIs are absent.
- API keys do not record `created_by_user_id`; list endpoints are limit-only and several endpoints return unbounded rows.
- There are no audit-log, ledger, attempt, payment-timeline, reconciliation-queue, or platform-admin APIs.
- Callback receipt is synchronous. Current processing is bounded and suitable for the pilot, but latency and database contention must be monitored before moving processing to a queue.
- Request IDs and payment IDs are not consistently injected into structured logs because application logging is still minimal.

## 4. Current database state

Alembic head is `0005_merchant_onboarding`. The schema contains organizations, users, sessions, password reset tokens, MFA credentials, email outbox, invitations, merchant accounts, encrypted Daraja credentials, hashed API keys, payments, attempts, callbacks, status checks, webhook endpoints/deliveries/attempts, ledger entries, and audit logs.

### Strengths

- Core uniqueness constraints cover organization/shortcode/environment, merchant external reference, merchant idempotency value, CheckoutRequestID, attempt number, attempt CheckoutRequestID, and merchant receipt number.
- Core payment and callback time indexes exist.
- Payment-plane RLS policies and production-style non-owner/BYPASSRLS role tests exist.
- Audit and ledger mutation is blocked by database triggers.

### Version 2 migration needs

- Add payment evidence/review/acceptance columns and indexed merchant/status/review/time access paths.
- Add retry policy fields or derive active retry from attempts under a payment row lock.
- Add email-verification token/state, consent fields, merchant approval/rejection/suspension evidence, and a safe platform-admin authorization mechanism.
- Add API-key creator metadata, webhook archived/disabled metadata, and delivery/attempt composite indexes.
- Add composite audit organization/created-at and callback processing-status/received-at indexes.
- Add cursor-pagination indexes and avoid destructive backfills. All new non-null fields require safe server defaults followed by constraint tightening where appropriate.
- Control-plane bootstrap tables remain outside RLS because tenant context is unknown during authentication. This is documented defense-in-depth debt, not a substitute for strict lookup code.

## 5. Current test state

Baseline run on 2026-07-16:

- `pytest -q`: **66 passed, 4 skipped** in 22.32 seconds.
- `make lint`: Ruff check, Ruff format check, and dashboard JavaScript syntax pass.
- `make security`: Ruff security rules, Bandit, and `pip-audit` pass.
- Alembic reports one head: `0005_merchant_onboarding`.

Coverage already includes payment creation/idempotency, provider rejection/uncertainty, callbacks, conflicting evidence, duplicate receipts, webhook retry/dead-letter/replay, reconciliation, encryption/KMS contracts, identity/session/MFA/reset, tenant isolation, and dashboard browser-security contracts.

The four skips are the opt-in PostgreSQL concurrency/RLS tests and Safaricom sandbox contract tests because their external database URL and dedicated sandbox credentials are not configured in this environment. There is no merchant-dashboard component/browser test suite yet.

## 6. Proposed Version 2 implementation sequence

1. **Migration and domain vocabulary:** add evidence, review, consent, email-verification, approval, API-key creator, and operational indexes in one reviewed additive migration.
2. **Payment correctness:** centralize STK attempt submission, implement row-locked safe retries, expose attempts/ledger/timeline, and complete idempotency conflict tests.
3. **Reconciliation:** implement elapsed-time backoff, explicit exhausted/manual-review state, issue filters, and reconciliation metrics.
4. **Onboarding and production control:** hashed email verification, versioned terms/privacy acceptance, separate platform-admin authorization, approval/reject/suspend endpoints, and audited activation gates.
5. **Developer controls:** split webhook read/write scopes; add endpoint update/archive/test/secret rotation and detailed deliveries; add API-key pagination, creator metadata, and complete environment rules.
6. **Operational APIs:** cursor-paginate list endpoints; add payment search/detail, reconciliation queue, audit log, merchant health, and platform-admin issue views.
7. **Merchant application:** build `apps/merchant-dashboard/` behind a same-origin BFF cookie boundary; implement auth/onboarding first, then payments/detail/reconciliation, keys/webhooks/audit, and minimal admin approval.
8. **Observability/deployment:** request correlation, domain counters/histograms, worker heartbeat, pilot Compose profile, environment validation, backup/restore runbook, and alert updates.
9. **Proof:** SQLite unit/API tests, migrated PostgreSQL/RLS/concurrency tests, frontend unit/browser tests, Docker integration test, controlled Daraja sandbox contract, load/failure injection, backup/restore drill, and security review.

Implementation should preserve synchronous raw-first callback processing for Version 2 unless measured callback latency or lock contention shows that queueing is required. An async callback-processing redesign is postponed to Version 3 because it adds delivery semantics and operational failure modes that are not justified without evidence at 100 businesses.

## 7. Blockers and explicit postponements

### Blockers to a production pilot

- The repository has no initial commit and every file is untracked. A reviewed baseline commit and protected remote branch are required before schema/application upgrades can be safely released or rolled back.
- Dedicated PostgreSQL test roles/URL, Safaricom sandbox credentials, SMTP provider credentials, AWS KMS/IAM access, production DNS/TLS, and monitoring destinations are external inputs not present locally.
- Platform-admin ownership and the first trusted approver identity must be defined before production approval can be enabled.
- Legal owners must provide the accepted terms/privacy text and version identifiers before live consent can be meaningful.

### Postponed beyond Version 2

- Card/bank/provider abstractions, wallets, lending, settlement, aggregation, SaaS billing, reseller/white-label features, marketplace flows, and complex accounting.
- Database partitioning and a fully asynchronous callback-processing pipeline unless pilot measurements require them.
- Automated merchant underwriting/KYB; Version 2 uses an explicit human approval gate.

## Assessment conclusion

The existing backend is a credible reliability foundation, but the product is **not yet ready for 100 live businesses**. The safest path is an additive Version 2 upgrade centered on retry/evidence semantics, independent production approval, email/consent proof, operational APIs, and a secure merchant-facing application. No broad rewrite or multi-provider layer is warranted.

# LynxPay Architecture

Status: Core and production-hardening baseline  
Last updated: 2026-07-16

LynxPay is a standalone, tenant-isolated M-PESA Daraja infrastructure service. It is not a payment aggregator. It never holds, pools, settles, transfers, or takes custody of merchant funds. Every merchant supplies and owns its own PayBill, Till, store number, shortcode, and Daraja credentials.

The service is API-first and independent of SmartLynxPOS. It has its own identity system, database, migrations, configuration, worker, dashboard, tests, and deployment boundary.

## Architectural invariants

1. An STK acceptance response is not payment success. Only a valid callback or verified Daraja status response can establish success.
2. Raw callback evidence is retained, including malformed, rejected, unmatched, and duplicate callbacks.
3. Every important payment or security event creates immutable ledger and/or audit evidence.
4. Tenant ownership is enforced in application queries and reinforced with PostgreSQL RLS on payment-plane data.
5. Merchant credentials, webhook secrets, MFA seeds, and sensitive email payloads are encrypted at rest.
6. Passwords, API keys, refresh tokens, password-reset tokens, invitation tokens, and MFA recovery codes are stored only as hashes or keyed digests.
7. Webhook deliveries are durable, signed, retryable, dead-lettered, and replayable.
8. Callback processing and payment success transitions are idempotent.
9. Payment references are unique within a merchant, and M-PESA receipt reuse cannot create multiple successful payments.
10. SmartLynxPOS migration must preserve one STK initiator per sale—never dual initiation or per-request fallback.

## Runtime topology

```text
Merchant application / SmartLynxPOS / merchant dashboard
                  |
                  | HTTPS: JWT or scoped API key
                  v
          +--------------------+
          | LynxPay FastAPI API|
          |--------------------|
          | identity and teams |
          | merchant control   |
          | STK orchestration  |
          | callback ingestion |
          | payment queries    |
          | metrics / health   |
          +----+----------+----+
               |          |
               |          +----------------> Redis
               |                            distributed rate limits
               v
          PostgreSQL
          system of record
               ^
               |
          +----+------------------+
          | LynxPay worker        |
          |-----------------------|
          | webhook dispatch      |-------> merchant HTTPS endpoints
          | Daraja reconciliation |-------> merchant-specific Daraja account
          | identity email        |-------> SMTP provider
          | STK recovery          |-------> unknown/manual-review queue
          +-----------------------+

API and worker ---------------------------> optional OTLP collector
Prometheus scraper -----------------------> protected /metrics
AWS KMS or local keyring -----------------> envelope-key wrapping
```

PostgreSQL is the durable system of record. Redis is not authoritative for payments; it currently coordinates distributed rate-limit counters. Queue work is stored in PostgreSQL and claimed with expiring leases.

## Service components

### API process

The FastAPI process owns synchronous control-plane and payment-plane requests:

- organization registration, authentication, MFA, password reset, and session management;
- merchant, Daraja credential, API-key, and team administration;
- STK Push creation and Daraja initiation;
- raw-first M-PESA callback intake;
- payment, callback, reconciliation, and audit queries;
- webhook endpoint registration and replay requests;
- readiness, health, and protected Prometheus metrics.

### Worker process

`python -m app.worker` processes durable PostgreSQL work:

- webhook deliveries;
- missing/ambiguous callback reconciliation;
- password-reset and invitation email delivery;
- recovery of STK submissions left ambiguous by process loss.

Production deploys independent `webhooks`, `reconciliation`, `email`, and `maintenance` worker modes. Workers claim rows with `FOR UPDATE SKIP LOCKED`, assign a lease owner and expiry, finish an in-flight item during graceful shutdown, and recover expired leases after a crash. Webhook claims are capped per endpoint to prevent one failing merchant from starving others; persistently failing endpoints are automatically paused. `WORKER_DATABASE_URL` is present only in worker processes, while API/admin/metrics credentials are absent from worker containers.

### Merchant dashboard

The merchant application in `apps/merchant-dashboard/` is an independently built and deployed Next.js service. It is Daraja-specific rather than a generic provider shell: the information hierarchy starts with payment state, captured M-PESA volume, requests awaiting evidence, callbacks, receipts, reconciliation, shortcode, and sandbox/live context.

The browser calls only the dashboard's same-origin `/api/lynxpay/*` routes. This backend-for-frontend forwards requests to FastAPI over the private service network, coalesces concurrent refresh rotations by token digest, and stores access and refresh tokens in `HttpOnly`, `Secure` in production, `SameSite=Strict` cookies. Browser JavaScript never receives or persists bearer tokens. Mutations fail closed unless Origin and Fetch Metadata establish a same-origin request, auth token endpoints cannot be reached through the generic proxy, and proxied responses are marked `no-store`. A Content Security Policy is set at the dashboard boundary; removing the remaining framework-compatible inline allowances through nonces is a further hardening item.

The Next.js and FastAPI runtimes have separate dependency manifests, containers, environment variables, and build pipelines. `LYNXPAY_API_URL` is a private server-only variable and must never use a `NEXT_PUBLIC_` prefix. Docker Compose deploys the Next.js service on the dashboard port and keeps FastAPI as the only authority for identity, tenancy, credentials, payment state, and audit events. The superseded dependency-free console was removed after the Next.js application gained callback, reconciliation, webhook, team/MFA, API-key, audit, and production-approval workflows.

## Data model

### Identity and control plane

| Model | Responsibility |
|---|---|
| `Organization` | Top-level tenant/customer account. |
| `User` | Native organization member with bcrypt password hash, role, and status. |
| `AuthSession` | Refresh-token family, hash, expiry, rotation lineage, and revocation state. |
| `PasswordResetToken` | Hashed, expiring, one-use password-reset capability. |
| `MfaTotpCredential` | Encrypted TOTP seed and hashed one-use recovery codes. |
| `TeamInvitation` | Hashed, expiring, revocable organization invitation. |
| `EmailOutbox` | Encrypted email payload, retry state, lease, and delivery outcome. |
| `MerchantAccount` | One merchant-owned PayBill, Till, or store-number configuration. |
| `DarajaCredential` | Versioned encrypted credentials for exactly one merchant and environment. |
| `ApiKey` | Key prefix, keyed digest, sandbox/production environment, scopes, optional merchant binding, status, and expiry. |

### Payment and evidence plane

| Model | Responsibility |
|---|---|
| `Payment` | One intended merchant payment and its verified result. |
| `PaymentAttempt` | One outbound STK attempt with redacted request and provider response. |
| `MpesaCallback` | Raw callback evidence and duplicate/processing metadata. |
| `PaymentStatusCheck` | Raw Daraja status-query response and reconciliation result. |
| `PaymentLedgerEntry` | Append-only state-event history; never a merchant balance. |
| `AuditLog` | Append-only sensitive-action evidence with user/API-key actor context. |

### Delivery plane

| Model | Responsibility |
|---|---|
| `WebhookEndpoint` | Merchant subscription and encrypted HMAC signing secret. |
| `WebhookDelivery` | Durable event delivery, retry, replay, lease, and dead-letter state. |
| `WebhookDeliveryAttempt` | Evidence for every outbound network attempt. |

## Authentication and session security

- Passwords use bcrypt through Passlib.
- Access tokens are short-lived HS256 JWTs containing a session ID.
- Every JWT-authenticated request verifies that the referenced database session is still active and unexpired; revoking the session invalidates the access token.
- Refresh tokens are high-entropy opaque values. Only a prefix and HMAC-SHA256 digest are stored.
- Refresh rotates on every successful use. Reuse of a rotated token revokes the entire token family.
- Password reset uses an indistinguishable `202` response, hashed one-use tokens, bounded expiry, encrypted email payloads, invalidates older pending reset tokens, and revokes all active sessions when completed.
- TOTP MFA uses encrypted seeds, rejects reuse of a previously accepted time step, and provides one-use hashed recovery codes. Full secrets and recovery codes are returned only at setup.
- Owner, admin, operator, developer, support, accountant, and read-only roles map to explicit scopes. Privileged control-plane and raw-callback access requires a recent MFA-authenticated session in production; API keys never receive raw callback bodies.
- API keys are high-entropy, scoped, explicitly sandbox or production, HMAC-SHA256 digested, and shown only once. Environment predicates prevent test keys from reaching live merchants, and live payment-write keys must be merchant-bound.
- Invitation tokens are hashed and expire; invitation email content is placed in the encrypted outbox.

The HS256 signing secret must be independently generated and rotated under change control. A future multi-service deployment should consider asymmetric JWT signing or a dedicated identity issuer.

## Tenant isolation and database roles

Every protected endpoint scopes records by `organization_id`. Merchant-bound API keys add an immutable merchant predicate, so a caller cannot escape its merchant by supplying another identifier. Cross-tenant object access returns `404` rather than revealing existence.

Production uses seven database identities:

1. Migration owner: owns schema changes and is not used for request traffic.
2. API role: `NOSUPERUSER NOBYPASSRLS`, with least-privilege DML grants.
3. Worker role: `NOSUPERUSER NOBYPASSRLS`; explicit role-scoped RLS policies allow only the cross-tenant queue and reconciliation work it must claim.
4. Platform-admin role: `NOSUPERUSER NOBYPASSRLS`; explicit policies support independently authorized operational review without exposing this identity to ordinary API sessions.
5. Metrics role: `NOSUPERUSER NOBYPASSRLS`, read-only, with explicit aggregate policies through `METRICS_DATABASE_URL`.
6. Read-only role: no write grants and ordinary tenant RLS unless a separately reviewed support workflow sets tenant context.
7. Cluster/bootstrap administrator: provisions roles only and is never supplied to an application container.

API, worker, platform-admin, and metrics connections fail production startup if their identity is a superuser, has `BYPASSRLS`, or owns payment-plane tables. API/admin/metrics URLs must be distinct. `PROCESS_TYPE` makes database requirements process-specific: the API container never receives the worker credential, and workers never receive platform-admin or metrics credentials. Role provisioning and post-migration grants live in `ops/provision-postgres-roles.sql` and `ops/apply-runtime-grants.sql`.

After authentication, the API sets transaction-local `app.organization_id`. PostgreSQL RLS independently constrains payments, status checks, credentials, payment attempts, callbacks, ledgers, audit logs, webhook endpoints, deliveries, and delivery attempts.

Authentication bootstrap tables—users, sessions, API-key prefixes, reset tokens, and invitations—must be located before the tenant is known, so they currently rely on narrowly scoped application queries rather than the payment-plane RLS policy. Extending database-enforced isolation to these tables requires an identity-specific schema/role or reviewed `SECURITY DEFINER` lookup functions; this remains a defense-in-depth improvement.

RLS does not replace application scoping, authorization tests, or separate credentials for API and worker processes.

PostgreSQL also rejects `UPDATE` and `DELETE` against payment-ledger and audit rows through database triggers, and those privileges are revoked from `PUBLIC`. The migration owner retains a controlled break-glass path; normal API/worker behavior is append-only.

## Encryption and key rotation

LynxPay uses envelope encryption:

1. Generate a random Fernet data key per credential/secrets bundle.
2. Encrypt each secret in that bundle locally with the same short-lived data key.
3. Wrap only the data key with a versioned local master key or AWS KMS key.
4. Store an `env1` envelope containing key version, wrapped data key, and ciphertext.

AWS KMS calls bind the encryption context to `service=lynxpay` and the exact key-version label. Credential bundles require one KMS wrap and one unwrap rather than one remote call per field; the provider/client is process-cached. Plaintext merchant secrets, MFA seeds, webhook secrets, and email payloads are never persisted or logged. API responses expose only masks.

`python -m app.rotate_encryption` supports dry-run and audited apply modes for Daraja credentials, webhook secrets, MFA seeds, and encrypted email payloads. Readers must receive both old and new versions before the active version changes. The old key remains decryptable through the rollback and backup-retention window.

The least-privilege runtime policy template is `ops/aws-kms-runtime-policy.json`; the controlled ceremony is documented in `docs/runbooks/kms-rotation.md`. Account IDs, role ARNs, key ARNs, CloudTrail evidence, and approval records must come from the target AWS account.

## STK Push lifecycle

Merchant onboarding follows `pending_setup -> credentials_added -> verified -> active`. Saving encrypted credentials does not activate the merchant. Credential testing must obtain a Daraja OAuth token. A human owner/admin must then initiate an exactly KES 1 `merchant_verification` payment and receive a valid success callback after the latest credential test before activation is allowed. API keys cannot initiate verification payments. Environment-configured public callback URLs cannot be replaced per merchant in production-style deployments.

1. Authenticate and authorize the organization/API-key scope.
2. Validate active merchant, environment-matching active credentials, positive whole-KES amount, Kenyan phone normalization, merchant reference uniqueness, and optional idempotency key.
3. Persist `Payment(created)`, ledger/audit evidence, transition to `pending`, and commit a redacted `PaymentAttempt(submitting)` before any provider network call.
4. Decrypt only that merchant's credentials and call the matching Daraja environment through an environment-isolated pooled HTTP client and single-flight OAuth token cache.
5. Persist Daraja response identifiers and evidence.
6. Move to `stk_sent` only when Daraja accepts the request and supplies a CheckoutRequestID.
7. Mark the attempt `accepted`, `rejected`, or `uncertain`. A definite pre-acceptance rejection becomes `failed`; a timeout, network ambiguity, 5xx, or malformed acceptance response becomes `unknown`. Each outcome writes ledger/audit evidence and queues the corresponding webhook event.
8. A maintenance worker converts stale `submitting` attempts to `abandoned` and the payment to `unknown` with audit/ledger evidence. It never silently retries the STK request.
9. Schedule accepted or ambiguous requests for reconciliation if no callback establishes a terminal result.

Daraja initiation never marks a payment successful. LynxPay does not automatically send a second STK request when acceptance is uncertain.

## Idempotency and uniqueness

| Boundary | Control |
|---|---|
| Client retry | A merchant-scoped HMAC digest of `Idempotency-Key` reuses the original durable result and returns `idempotent_replay=true`. The raw key is not retained. |
| Merchant reference | Unique `(merchant_account_id, external_reference)`. |
| Checkout request | Unique CheckoutRequestID where present. |
| Successful receipt | Unique `(merchant_account_id, mpesa_receipt_number)` where present. |
| Callback repeat | Success duplicates require the same checkout ID, receipt, amount, and phone; failure duplicates require the same checkout ID, code, and description. Verification failures never suppress later valid evidence. Conflicting receipts are retained and audited. |
| State transition | Payment row locking plus the explicit state machine permits one success transition and one success-ledger event. |
| Webhook replay | Creates a new delivery linked to the source; it does not mutate old attempt history. |

Database constraints remain the final concurrency guard when multiple API or callback workers race.

## Callback lifecycle

1. Resolve the merchant from the callback route and apply the callback-specific rate limit.
2. Stream and drain the request under a 64 KiB default storage limit. Oversized evidence is truncated, classified, audited, and rejected with `413`.
3. Preserve raw body, parsed payload when possible, source IP, receipt/result identifiers, normalized callback amount/phone, and callback-received audit evidence.
4. Verify the configured Safaricom CIDR allowlist or signing-proxy HMAC mode.
5. Match by merchant and CheckoutRequestID and lock the payment row.
6. Classify the row as `source_rejected`, `malformed`, `unmatched`, `verification_failed`, `processed_success`, `processed_failure`, `duplicate`, or `conflict`.
7. For success, require `ResultCode=0`, a receipt, matching amount, matching phone when provided, receipt uniqueness, and an allowed state transition.
8. Atomically commit callback state, payment state, ledger entry, audit event, and webhook outbox rows.

Malformed, rejected, failed, unmatched, duplicate, and conflicting callback rows remain queryable evidence. Raw payloads require a recent MFA-authenticated user with the separate `callbacks:read_raw` scope; ordinary callback readers and all API keys receive normalized fields only. A platform-admin-only manual-link workflow can attach unmatched callbacks after exact merchant, amount, phone, receipt, and provider-evidence checks; the reason and actor are immutable audit evidence and a callback cannot be linked twice.

## Payment state machine

```text
created -> pending -> stk_sent -> success -> reversed
              |          |-> failed
              |          |-> timeout
              |          `-> unknown -> success
              |                      `-> failed
              |-> failed  (definitely not sent/rejected)
              `-> unknown (provider acceptance uncertain)

cancelled  (terminal; reserved for an explicit future cancellation workflow)
```

`success -> pending`, `success -> failed`, `success -> timeout`, `failed -> success`, and `cancelled -> success` are rejected. Corrections require a separate explicit, audited workflow; ordinary callback processing cannot override terminal state.

## Reconciliation lifecycle

The worker briefly leases due `stk_sent` or `unknown` payments, commits the lease and evidence snapshot, releases all row locks, and only then calls Daraja's STK status-query API with the selected merchant's own credentials. It reacquires the lease and current payment evidence before applying the answer. A callback-confirmed success that races the provider call always wins. Every provider response, superseded result, or transport ambiguity creates a `PaymentStatusCheck` and audit event.

- Only verified Daraja `ResultCode=0` can transition an eligible payment to `success`.
- A verified known non-zero result maps through a centralized provider taxonomy to `failed`, `timeout`, or retry/manual review. Unknown provider codes become `unknown`, never an assumed failure.
- Ambiguous checks retain state and retry.
- Exhausted `stk_sent` records become `unknown`, never success.
- `unknown` remains visible for reconciliation or manual review and never triggers automatic re-initiation.

## Webhook lifecycle

Endpoint creation generates a signing secret shown once. Payment events create durable delivery rows in the same database transaction as state evidence.

The dispatcher provides:

- expiring PostgreSQL leases and `FOR UPDATE SKIP LOCKED` claiming;
- canonical JSON payloads and `HMAC-SHA256(timestamp + "." + body)` signatures;
- unique delivery IDs for receiver-side idempotency;
- bounded exponential backoff with jitter;
- durable attempt history, delivered state, and dead-letter state;
- replay as a new linked delivery;
- per-endpoint fair claiming, failure counters, automatic pause, and operator-visible endpoint health;
- DNS resolution and redirect controls blocking private, loopback, link-local, and cloud-metadata targets;
- original Host/TLS SNI preservation when connecting to a validated IP;
- strict connection, total-time, and response-size limits.

Redirects are not followed. A merchant receiver must verify the exact body, signature timestamp, freshness window, and delivery ID before applying the event.

## Email lifecycle

Password-reset and invitation endpoints store encrypted template payloads in `EmailOutbox`. The destination address remains available to the worker for routing; reset/invitation capabilities are contained only inside the encrypted payload and are never written to failure text.

SMTP delivery uses STARTTLS with the platform trust store, optional authenticated SMTP, bounded retries, leases, and dead-letter state. Stored delivery errors contain the exception class rather than provider responses that may include secrets. Production must integrate provider bounce, complaint, and suppression handling.

## Rate limiting and abuse controls

Redis provides a distributed fixed-window counter separated into authentication, callback-ingress, and general API classes. Identities are derived from a digest of the bearer/API key or the trusted client IP. Production startup requires Redis-backed limiting and fails closed if the limiter becomes unavailable. Health, readiness, and protected metrics have separate operational handling.

Rate limiting is a coarse abuse boundary, not merchant quota or SaaS billing. Merchant-specific quotas and adaptive controls remain outside the current scope.

## Observability and operations

Prometheus metrics include:

- request count, status, and latency;
- rate-limit rejections;
- payments by state;
- webhook and email deliveries by state;
- callback processing state and reconciliation backlog;
- stale STK submissions and paused webhook endpoints;
- database-gauge collection failures.

`/metrics` requires a bearer token in production and a separate platform metrics database connection. OpenTelemetry can instrument FastAPI, HTTPX, and SQLAlchemy and export through OTLP. Raw callbacks, authorization headers, API keys, phone numbers, email payloads, and decrypted secrets must never be attached to logs, metrics, or trace attributes.

Alert rules cover API error rate, latency, rate-limit spikes, webhook/email dead letters, unknown payments, and metric collection failures. Runbooks cover incident response, backup/restore, observability, KMS rotation, and safe SmartLynxPOS migration.

## Deployment and configuration

- PostgreSQL 16 is the required production database.
- Alembic migrations run once with the migration-owner identity before API/worker rollout.
- API and worker are separate processes with separate database credentials.
- `PROCESS_TYPE` validates API and worker configuration independently so no runtime is given unrelated database credentials.
- The runtime image runs as an unprivileged user and never auto-runs migrations; the migration job is a separate deployment step.
- Redis is required for production distributed rate limiting.
- Secrets and provider endpoints come exclusively from environment or the platform secret manager.
- Production requires HTTPS `PUBLIC_BASE_URL`, a strong JWT/HMAC key, versioned encryption configuration, protected metrics, callback-source verification with a non-empty allowlist or signing proxy, SMTP delivery, and explicit allowed origins/proxy CIDRs.
- API documentation is disabled in production.
- PostgreSQL backups contain personal data, raw callbacks, token hashes, and encrypted credentials and must remain encrypted and access-controlled.

## API surface

### Identity

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- `POST /api/v1/auth/mfa/setup`
- `POST /api/v1/auth/mfa/confirm`
- `DELETE /api/v1/auth/mfa`
- `GET /api/v1/auth/sessions`
- `DELETE /api/v1/auth/sessions/{session_id}`
- `DELETE /api/v1/auth/sessions`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/invitations/{token}/accept`

### Team management

- `GET /api/v1/team/users`
- `PATCH /api/v1/team/users/{user_id}`
- `POST /api/v1/team/invitations`
- `GET /api/v1/team/invitations`
- `DELETE /api/v1/team/invitations/{invitation_id}`

### Merchant and credentials

- `GET /api/v1/organization`
- `PATCH /api/v1/organization`
- `POST /api/v1/merchants`
- `GET /api/v1/merchants`
- `GET /api/v1/merchants/{merchant_id}`
- `PATCH /api/v1/merchants/{merchant_id}`
- `POST /api/v1/merchants/{merchant_id}/daraja-credentials`
- `PATCH /api/v1/merchants/{merchant_id}/daraja-credentials`
- `POST /api/v1/merchants/{merchant_id}/daraja-credentials/test`
- `DELETE /api/v1/merchants/{merchant_id}/daraja-credentials`

### API keys and payments

- `POST /api/v1/api-keys`
- `GET /api/v1/api-keys`
- `DELETE /api/v1/api-keys/{api_key_id}`
- `POST /api/v1/payments/stk-push`
- `GET /api/v1/payments`
- `GET /api/v1/payments/{payment_id}`
- `POST /api/v1/payments/{payment_id}/reconcile`
- `GET /api/v1/payments/{payment_id}/status-checks`

### Callbacks and webhooks

- `POST /api/v1/callbacks/mpesa/{merchant_id}`
- `GET /api/v1/callbacks`
- `GET /api/v1/callbacks/{callback_id}`
- `POST /api/v1/webhooks/endpoints`
- `GET /api/v1/webhooks/endpoints`
- `PATCH /api/v1/webhooks/endpoints/{endpoint_id}`
- `GET /api/v1/webhooks/deliveries`
- `POST /api/v1/webhooks/deliveries/{delivery_id}/replay`

### Operations

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `POST /api/v1/admin/callbacks/{callback_id}/link-payment`
- `POST /api/v1/admin/merchants/{merchant_id}/approve`
- `POST /api/v1/admin/merchants/{merchant_id}/reject`
- `POST /api/v1/admin/merchants/{merchant_id}/suspend`

## SmartLynxPOS integration boundary

The future flow is:

1. SmartLynxPOS creates a sale and immutable payment identity.
2. POS sends one LynxPay STK request with that identity as `external_reference` and a stable idempotency key.
3. LynxPay initiates through that merchant's own credentials.
4. LynxPay receives callback and/or reconciles status.
5. POS finalizes only from `payment.success`, learned by signed webhook or polling.

Migration is merchant-by-merchant. At each cutover, direct initiation pauses, old in-flight requests drain, and the routing flag changes atomically for future sales. A LynxPay-routed sale never falls back to direct Daraja, even after an ambiguous response. Rollback is another drained cutover boundary, not a per-request retry strategy.

## Validation status

The current local production-style baseline has verified:

- **106 passed, 4 skipped** in the full Docker/PostgreSQL-backed suite at Alembic head `0012_v2_ledger_coupling`;
- simultaneous callback processing with one success transition/ledger event;
- tenant isolation using real `NOSUPERUSER NOBYPASSRLS` API and worker roles, including role-scoped worker policies;
- database-trigger rejection of ledger/audit mutation and environment-isolated API keys;
- database-trigger rejection of a payment status commit without a matching ledger event;
- durable pre-network STK attempt state, stale-attempt recovery, provider taxonomy, evidence-aware callback idempotency, callback rate classes, trusted-proxy handling, oversized callback handling, and raw-evidence scope separation;
- reconciliation/callback race handling, Daraja token single-flight caching, pooled clients, and credential-bundle envelope encryption;
- Redis-backed distributed rate limiting;
- explicit RBAC, privileged MFA, refresh single-flight, session revocation, password reset, encrypted email queueing, and rotation coverage;
- dashboard payment/callback/webhook/team/approval operations, TypeScript, lint, unit, CSP, cookie, and request-origin contracts;
- resumable six-step merchant onboarding, business profiles, PayBill/Till setup, credential verification, KES 1 callback proof before activation, one-time API-key handoff, and production-mode warnings;
- retention reporting that defaults to no deletion and long-lived audit/ledger evidence.
- a bounded current-artifact HTTP read probe of 500 requests at concurrency 20 with zero failures, 148.3 requests/second, 127.1 ms mean, and 202.8 ms p95 on the local development stack; this is smoke evidence, not a production capacity claim.

The four skips are opt-in Safaricom sandbox contract cases requiring dedicated credentials and explicit live-STK consent. Exact final lint, build, dependency-audit, and load-probe results belong in the implementation handoff; they must not be inferred from this document.

See `docs/v2-live-pilot-validation-2026-07-16.md` for the exact commands, failures, skips, role drill, bounded probe, and launch decision from this hardening pass.

## Remaining production gates

No production-readiness claim is made until these are completed:

- run controlled Safaricom OAuth/status contracts with dedicated sandbox credentials and a known CheckoutRequestID;
- render and deploy the KMS policy with real role/key ARNs, execute a live rotation, verify CloudTrail, and decrypt a restored backup;
- validate real SMTP delivery, bounce/complaint processing, and the production OTLP/paging path;
- expand RLS or database-enforced controls to identity/control-plane bootstrap lookups;
- replace the dashboard CSP inline allowances with reviewed per-request nonces and complete independent assistive-technology testing;
- run sustained soak, multi-node failure, capacity, disaster-recovery, and regional failover exercises;
- complete independent penetration testing and assistive-technology accessibility review;
- execute the documented merchant-by-merchant SmartLynxPOS migration with real source systems and operators.

These gates require external credentials, infrastructure, source systems, change approvals, or independent assessors that are not present in this repository.

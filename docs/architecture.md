# LynxPay Architecture

Status: Core and production-hardening baseline  
Last updated: 2026-07-15

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
- password-reset and invitation email delivery.

Workers claim rows with `FOR UPDATE SKIP LOCKED`, assign a lease owner and expiry, and recover expired leases after a crash. `WORKER_DATABASE_URL` is separate from the API connection so production can give the worker controlled cross-tenant authority without giving it to the API.

### Merchant dashboard

The merchant application in `apps/merchant-dashboard/` is an independently built and deployed Next.js service. It is Daraja-specific rather than a generic provider shell: the information hierarchy starts with payment state, captured M-PESA volume, requests awaiting evidence, callbacks, receipts, reconciliation, shortcode, and sandbox/live context.

The browser calls only the dashboard's same-origin `/api/lynxpay/*` routes. This backend-for-frontend forwards requests to FastAPI over the private service network, rotates refresh tokens server-side, and stores access and refresh tokens in `HttpOnly`, `Secure` in production, `SameSite=Lax` cookies. Browser JavaScript never receives or persists bearer tokens. Mutations reject cross-origin requests, auth token endpoints cannot be reached through the generic proxy, and proxied responses are marked `no-store`.

The Next.js and FastAPI runtimes have separate dependency manifests, containers, environment variables, and build pipelines. `LYNXPAY_API_URL` is a private server-only variable and must never use a `NEXT_PUBLIC_` prefix. Docker Compose deploys the Next.js service on the dashboard port and keeps FastAPI as the only authority for identity, tenancy, credentials, payment state, and audit events. The older dependency-free `dashboard/` console remains only as temporary migration coverage and is not part of the deployed runtime.

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
- API keys are high-entropy, scoped, explicitly sandbox or production, HMAC-SHA256 digested, and shown only once. Environment predicates prevent test keys from reaching live merchants, and live payment-write keys must be merchant-bound.
- Invitation tokens are hashed and expire; invitation email content is placed in the encrypted outbox.

The HS256 signing secret must be independently generated and rotated under change control. A future multi-service deployment should consider asymmetric JWT signing or a dedicated identity issuer.

## Tenant isolation and database roles

Every protected endpoint scopes records by `organization_id`. Merchant-bound API keys add an immutable merchant predicate, so a caller cannot escape its merchant by supplying another identifier. Cross-tenant object access returns `404` rather than revealing existence.

Production uses four database identities:

1. Migration owner: owns schema changes and is not used for request traffic.
2. API role: `NOSUPERUSER NOBYPASSRLS`, with least-privilege DML grants.
3. Worker role: `NOSUPERUSER BYPASSRLS`, isolated to the worker and controlled maintenance jobs because it must claim work across tenants.
4. Metrics role: read-only controlled cross-tenant aggregate access through `METRICS_DATABASE_URL`; platform-wide gauges never run through an ordinary tenant-scoped API session.

After authentication, the API sets transaction-local `app.organization_id`. PostgreSQL RLS independently constrains payments, status checks, credentials, payment attempts, callbacks, ledgers, audit logs, webhook endpoints, deliveries, and delivery attempts.

Authentication bootstrap tables—users, sessions, API-key prefixes, reset tokens, and invitations—must be located before the tenant is known, so they currently rely on narrowly scoped application queries rather than the payment-plane RLS policy. Extending database-enforced isolation to these tables requires an identity-specific schema/role or reviewed `SECURITY DEFINER` lookup functions; this remains a defense-in-depth improvement.

RLS does not replace application scoping, authorization tests, or separate credentials for API and worker processes.

PostgreSQL also rejects `UPDATE` and `DELETE` against payment-ledger and audit rows through database triggers, and those privileges are revoked from `PUBLIC`. The migration owner retains a controlled break-glass path; normal API/worker behavior is append-only.

## Encryption and key rotation

LynxPay uses envelope encryption:

1. Generate a random Fernet data key per encrypted value.
2. Encrypt the secret locally with that data key.
3. Wrap only the data key with a versioned local master key or AWS KMS key.
4. Store an `env1` envelope containing key version, wrapped data key, and ciphertext.

AWS KMS calls bind the encryption context to `service=lynxpay` and the exact key-version label. Plaintext merchant secrets, MFA seeds, webhook secrets, and email payloads are never persisted or logged. API responses expose only masks.

`python -m app.rotate_encryption` supports dry-run and audited apply modes for Daraja credentials, webhook secrets, MFA seeds, and encrypted email payloads. Readers must receive both old and new versions before the active version changes. The old key remains decryptable through the rollback and backup-retention window.

The least-privilege runtime policy template is `ops/aws-kms-runtime-policy.json`; the controlled ceremony is documented in `docs/runbooks/kms-rotation.md`. Account IDs, role ARNs, key ARNs, CloudTrail evidence, and approval records must come from the target AWS account.

## STK Push lifecycle

Merchant onboarding follows `pending_setup -> credentials_added -> verified -> active`. Saving encrypted credentials does not activate the merchant. Credential testing must obtain a Daraja OAuth token. A human owner/admin must then initiate an exactly KES 1 `merchant_verification` payment and receive a valid success callback after the latest credential test before activation is allowed. API keys cannot initiate verification payments. Environment-configured public callback URLs cannot be replaced per merchant in production-style deployments.

1. Authenticate and authorize the organization/API-key scope.
2. Validate active merchant, environment-matching active credentials, positive whole-KES amount, Kenyan phone normalization, merchant reference uniqueness, and optional idempotency key.
3. Persist `Payment(created)`, ledger/audit evidence, transition to `pending`, and create a redacted `PaymentAttempt`.
4. Decrypt only that merchant's credentials and call the matching Daraja environment.
5. Persist Daraja response identifiers and evidence.
6. Move to `stk_sent` only when Daraja accepts the request and supplies a CheckoutRequestID.
7. A definite pre-acceptance rejection becomes `failed`; a timeout, network ambiguity, 5xx, or malformed acceptance response becomes `unknown`. Each outcome writes ledger/audit evidence and queues the corresponding webhook event.
8. Schedule accepted or ambiguous requests for reconciliation if no callback establishes a terminal result.

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

Malformed, rejected, failed, unmatched, duplicate, and conflicting callback rows remain queryable evidence. Raw payloads require the separate `callbacks:read_raw` scope; ordinary callback readers receive normalized fields only.

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

The worker leases due `stk_sent` or `unknown` payments and calls Daraja's STK status-query API with the selected merchant's own credentials. Every provider response or transport ambiguity creates a `PaymentStatusCheck` and audit event.

- Only verified Daraja `ResultCode=0` can transition an eligible payment to `success`.
- A verified non-zero result maps to `failed` or `timeout` according to the result.
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
- database-gauge collection failures.

`/metrics` requires a bearer token in production and a separate platform metrics database connection. OpenTelemetry can instrument FastAPI, HTTPX, and SQLAlchemy and export through OTLP. Raw callbacks, authorization headers, API keys, phone numbers, email payloads, and decrypted secrets must never be attached to logs, metrics, or trace attributes.

Alert rules cover API error rate, latency, rate-limit spikes, webhook/email dead letters, unknown payments, and metric collection failures. Runbooks cover incident response, backup/restore, observability, KMS rotation, and safe SmartLynxPOS migration.

## Deployment and configuration

- PostgreSQL 16 is the required production database.
- Alembic migrations run once with the migration-owner identity before API/worker rollout.
- API and worker are separate processes with separate database credentials.
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

## SmartLynxPOS integration boundary

The future flow is:

1. SmartLynxPOS creates a sale and immutable payment identity.
2. POS sends one LynxPay STK request with that identity as `external_reference` and a stable idempotency key.
3. LynxPay initiates through that merchant's own credentials.
4. LynxPay receives callback and/or reconciles status.
5. POS finalizes only from `payment.success`, learned by signed webhook or polling.

Migration is merchant-by-merchant. At each cutover, direct initiation pauses, old in-flight requests drain, and the routing flag changes atomically for future sales. A LynxPay-routed sale never falls back to direct Daraja, even after an ambiguous response. Rollback is another drained cutover boundary, not a per-request retry strategy.

## Validation status

The local production-style baseline has verified:

- **67 passed, 2 skipped** in the full PostgreSQL-backed test suite at Alembic head `0005_merchant_onboarding`;
- simultaneous callback processing with one success transition/ledger event;
- tenant isolation using real `NOSUPERUSER NOBYPASSRLS` API and `NOSUPERUSER BYPASSRLS` worker roles;
- database-trigger rejection of ledger/audit mutation and environment-isolated API keys;
- provider rejection/uncertainty semantics, evidence-aware callback idempotency, oversized callback handling, and raw-evidence scope separation;
- Redis-backed distributed rate limiting;
- bounded PostgreSQL-backed load with zero failed requests;
- database pause/recovery and backup/restore to the current Alembic head;
- refresh rotation, session revocation, password reset, MFA, encrypted email queueing, and rotation coverage;
- dashboard semantic, contrast, focus, reduced-motion, JavaScript, CSP, and security-header contracts;
- resumable six-step merchant onboarding, business profiles, PayBill/Till setup, credential verification, KES 1 callback proof before activation, one-time API-key handoff, and production-mode warnings;
- a clean Docker test runner, lint/format/security rules, Bandit, and a dependency audit with no known vulnerabilities at the successful audit point.

See `docs/validation-2026-07-15.md` for commands, measurements, skips, and environment-related failures.

## Remaining production gates

No production-readiness claim is made until these are completed:

- run controlled Safaricom OAuth/status contracts with dedicated sandbox credentials and a known CheckoutRequestID;
- render and deploy the KMS policy with real role/key ARNs, execute a live rotation, verify CloudTrail, and decrypt a restored backup;
- validate real SMTP delivery, bounce/complaint processing, and the production OTLP/paging path;
- expand RLS or database-enforced controls to identity/control-plane bootstrap lookups;
- replace browser bearer-token storage with a reviewed production session boundary;
- run sustained soak, multi-node failure, capacity, disaster-recovery, and regional failover exercises;
- complete independent penetration testing and assistive-technology accessibility review;
- execute the documented merchant-by-merchant SmartLynxPOS migration with real source systems and operators.

These gates require external credentials, infrastructure, source systems, change approvals, or independent assessors that are not present in this repository.

# Validation record — 2026-07-15

Environment: local Docker Engine, PostgreSQL 16, Redis 7, Python test runner, Nginx 1.27, and Node. External provider credentials were intentionally not invented.

## Passed

- Full suite against a disposable migrated PostgreSQL database: **67 passed, 2 skipped**.
- Clean `Dockerfile.test`/Compose run against a fresh PostgreSQL 16 container: **67 passed, 2 skipped** in 50.60 seconds (plus one upstream Passlib/Python `crypt` deprecation warning).
- Alembic upgrade through `0005_merchant_onboarding` and downgrade to base: passed on SQLite; PostgreSQL test fixtures also migrated to head.
- Merchant onboarding contracts passed for organization profile persistence/audit, six-step dashboard coverage, administrator-only KES 1 verification, callback proof before activation, and API-key handoff wiring.
- Launch-hardening contracts passed for definite/uncertain STK failures, non-zero/malformed acceptance responses, evidence-aware callback duplicates/conflicts, invalid-then-valid callbacks, callback body limits, API-key environment isolation, raw-callback scope separation, merchant verification lifecycle, Till setup, and webhook endpoint/delivery management.
- Simultaneous successful callbacks: one payment success transition and one success-ledger event.
- Real database roles: API role `NOSUPERUSER NOBYPASSRLS`; worker role `NOSUPERUSER BYPASSRLS`. Tenant reads and writes were isolated and the worker role could read cross-tenant work rows.
- Ledger/audit mutation through the restricted role was rejected by PostgreSQL trigger and grant controls.
- Identity: password reset, encrypted email outbox, TOTP MFA, one-use recovery codes, refresh rotation/reuse detection, invitation flow, and session revocation.
- Encryption rotation: dry-run/apply coverage includes Daraja credentials, webhook secrets, MFA seeds, and encrypted email payloads; audit rows are asserted.
- Real Redis rate limiting: five identical auth requests returned `202, 202, 202, 429, 429`; the distributed Redis key was present.
- Protected Prometheus endpoint: unauthenticated `401`, bearer-authenticated `200`; database-backed queue/payment gauges are bounded by status.
- Bounded local load probe: 500 PostgreSQL-backed payment-list requests, concurrency 20, 0 failures, 122.4 requests/second, mean 158.4 ms, p95 267.9 ms.
- PostgreSQL failure injection: readiness was `200`, failed while PostgreSQL was paused, and recovered to `200` after unpause.
- Backup/restore drill: PostgreSQL 16 custom-format dump and checksum, isolated restore, Alembic head verification, and seeded-row integrity (`1|1|0003_identity_security`).
- Worker recovery: expired webhook leases were reclaimed; SMTP failure stored only the exception class and scheduled a retry without persisting provider/token text.
- Dashboard: registration, PayBill/Till fields, credential test/activation flow, production warning, static semantic/accessibility/contrast tests, JavaScript syntax check, responsive/reduced-motion/focus behavior, same-origin API proxy, hardened Nginx headers, and SPA fallback contracts passed.
- Ruff application/test lint and security rules (`S` selection): passed.
- Ruff formatting, Bandit, and dependency audit: passed; `pip-audit -r requirements.txt` reported no known vulnerabilities.

## Skipped or blocked

- **2 Safaricom sandbox tests skipped**: no dedicated sandbox consumer key/secret/passkey/shortcode and existing CheckoutRequestID were available. No Safaricom request was sent.
- **Live AWS KMS drill blocked**: no AWS credentials, account/role ARNs, KMS key ARNs, CloudTrail destination, or approved IAM policy deployment exists in this workspace. The adapter and rotation are tested locally/mocked; that is not a live-KMS result.
- **SMTP and OTLP provider contracts blocked**: no dedicated SMTP or telemetry collector credentials/endpoints were available.
- **SmartLynxPOS cutover blocked**: this clean repository contains no POS source, merchant inventory, credentials, operators, production endpoints, or Git remote. The no-fallback/no-dual-initiation migration runbook is ready, but no merchant was migrated.
- **Independent penetration/accessibility testing not performed**: automated security and WCAG contract checks are not substitutes for independent penetration testing or assistive-technology review.

## Validation constraints

- The earlier backup/restore and bounded-load measurements predate migration `0004`; migration correctness is rechecked, but those operational drills must be repeated on the release candidate rather than inferred.

No production-readiness claim is made.

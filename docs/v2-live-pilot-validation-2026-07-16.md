# LynxPay v2 Live-Pilot Validation

Date: 2026-07-16  
Scope: Daraja-only payment infrastructure; no aggregation, custody, settlement, payment links, or SaaS billing.

## 1. Executive summary

This hardening pass closes the highest-risk repository-level payment correctness gaps: an STK request is durable before the provider call, process loss becomes an explicit abandoned/unknown case, unmatched callbacks can be linked only through evidence-checked platform operations, reconciliation does not hold a row lock during Daraja network I/O, and PostgreSQL now enforces payment-status/ledger coupling.

The repository is materially stronger and suitable for internal sandbox use. It is not unconditionally ready for live merchants because dedicated Safaricom sandbox contracts, real AWS KMS/IAM rotation, isolated production-worker smoke tests, SMTP/OTLP/paging validation, sustained load/failure/restore drills, and independent security/accessibility testing require external systems not present in this workspace.

Maximum safe scope today: internal sandbox and controlled staging. A supervised 10-merchant live pilot is a **Conditional Go only after the external launch gates below pass**.

## 2. Architecture and payment correctness changes

- STK attempts persist and commit as `submitting` before any Daraja call, then become `accepted`, `rejected`, `uncertain`, or `abandoned`.
- Stale submission recovery never sends a second STK request; it moves eligible payments to `unknown`/manual review and writes ledger, audit, and webhook evidence.
- A platform-admin callback-link route accepts only unmatched evidence and requires matching merchant, amount, phone/receipt evidence, provider request evidence for failures, receipt uniqueness, an allowed payment state, recent privileged MFA, and a reason.
- Callback ingress uses raw-first storage, trusted-proxy-aware source IPs, per-merchant/source rate keys, a high verified-provider budget, and a smaller unverified/malformed budget.
- Duplicate callback, duplicate CheckoutRequestID, duplicate receipt, conflicting success evidence, and terminal-state protections remain database/application enforced.
- Unknown Daraja result codes map to `unknown`/review rather than assumed failure.
- Reconciliation is two-phase: commit lease/snapshot, release locks, call Daraja, reacquire current evidence, and allow callback-confirmed success to win races.
- Payment status changes require a matching ledger event in SQLAlchemy and a deferred PostgreSQL constraint trigger.

## 3. Database migrations

- `0009_v2_submission_recovery`: STK submission timestamps/recovery evidence and callback-link actor/reason.
- `0010_v2_webhook_endpoint_health`: consecutive failures, pause time, and pause reason.
- `0011_v2_rbac_mfa_sessions`: session MFA assurance and explicit-role migration.
- `0012_v2_ledger_coupling`: deferred PostgreSQL status/ledger integrity trigger.
- `0006` and `0007` were made SQLite batch-migration safe by naming added foreign keys; `0009` now uses batch constraint creation/removal.

Alembic head: `0012_v2_ledger_coupling`.

## 4. Runtime, workers, and database roles

- Production compose defines separate webhook, reconciliation, email, and maintenance worker processes.
- Workers support explicit `--mode`, expiring lease recovery, bounded claims, graceful SIGTERM/SIGINT completion, and durable heartbeats.
- Webhook claiming is fair per endpoint; repeatedly failing endpoints auto-pause instead of creating an unbounded retry storm.
- `PROCESS_TYPE` makes production configuration process-specific. The API does not receive `WORKER_DATABASE_URL`; workers do not receive platform-admin or metrics credentials.
- Provisioning defines migration-owner, migrator, API, worker, platform-admin, metrics, and read-only roles. Runtime roles are `NOSUPERUSER NOBYPASSRLS` and do not own payment tables.
- An isolated real-role drill successfully provisioned roles, migrated through head as the non-superuser migrator/owner boundary, applied runtime grants, and verified all four runtime roles had `rolsuper=false`, `rolbypassrls=false`, with zero runtime-owned LynxPay tables.

Individual worker-mode smoke starts were not run against the populated development database because webhook, reconciliation, and email workers may perform real outbound calls for existing queue rows. That check must run against an isolated staging database/endpoints.

## 5. Daraja and credential-vault changes

- OAuth tokens are cached per environment/credential digest with single-flight refresh and expiry safety.
- HTTPX clients are pooled by event loop/environment and closed during application shutdown.
- OAuth, STK, and status-query timings use low-cardinality operation/environment metrics.
- Credential bundles reuse one envelope data key, reducing KMS wrap/unwrap hot-path calls while retaining versioned encryption context.
- KMS provider/client construction is cached; rotation supports grouped fields and legacy ciphertext.
- No plaintext credential response/log behavior was added.

Real AWS KMS calls, final IAM policy deployment, CloudTrail review, restored-backup decrypt, and a complete key-rotation drill were not possible without the target AWS account.

## 6. Identity, RBAC, and dashboard security

- Explicit scopes exist for owner, admin, operator, developer, support, accountant, and read-only roles.
- Production requires recent MFA for privileged control-plane access; API keys can never read raw callback bodies.
- Refresh-token rotation preserves MFA assurance and the dashboard BFF coalesces concurrent refreshes by token digest.
- Dashboard cookies are HttpOnly, Secure in production, and SameSite Strict.
- Mutating BFF requests fail closed on missing/cross-origin Origin or Fetch Metadata; the generic proxy cannot reach auth token endpoints.
- CSP and security headers are present. Framework-compatible inline CSP allowances remain and should be replaced by reviewed nonces.
- Added callback investigation, webhook endpoint/delivery health and replay, team/MFA, and active-merchant suspension operations.
- Existing onboarding, payments, reconciliation, API-key, audit, approval, and credential workflows remain Daraja-specific.

## 7. Observability and retention

- Metrics cover payment states and explicit pending/unknown counts, callback states, stale submissions, reconciliation backlog/oldest age, webhook state/oldest age, paused endpoints, worker heartbeat age, database connections, Daraja latency, OAuth cache results, and delivery/reconciliation outcomes.
- Alerts now cover callback rate rejection, stale submissions, unknown/reconciliation backlog, webhook age/dead letters/paused endpoints, worker heartbeat, database pool pressure, Daraja error rate, API 5xx, and latency.
- Incident procedures document missing-payment investigation, unmatched callback linking, safe STK retry decisions, webhook replay/pause, merchant approval/suspension, credential rotation, and database/worker response.
- Retention is report-only and deletion-disabled by default: callbacks/status evidence 400 days, webhook delivery evidence 180 days, and audit/ledger evidence 2,555 days. Archive/legal approval is required before deletion is implemented.

## 8. Files created

- `.github/workflows/ci.yml`
- `docker-compose.production.yml`
- `alembic/versions/0009_v2_submission_recovery.py`
- `alembic/versions/0010_v2_webhook_endpoint_health.py`
- `alembic/versions/0011_v2_rbac_mfa_sessions.py`
- `alembic/versions/0012_v2_ledger_coupling.py`
- `app/database_roles.py`, `app/maintenance.py`, `app/provider_codes.py`
- `ops/provision-postgres-roles.sql`, `ops/apply-runtime-grants.sql`
- `docs/data-retention.md` and this validation report
- dashboard callback and team pages
- dashboard refresh-single-flight and request-security modules/tests
- v2 callback-ingress, database-role, and provider-code backend tests

## 9. Important files changed

- Backend: `app/router.py`, `app/admin.py`, `app/models.py`, `app/reconciliation.py`, `app/webhooks.py`, `app/worker.py`, `app/daraja.py`, `app/observability.py`.
- Security/config: `app/auth.py`, `app/deps.py`, `app/core/config.py`, `app/core/deps.py`, `app/core/security.py`, `app/rotate_encryption.py`, `.env.example`.
- Dashboard: BFF route/library, navigation/icons, admin merchant operations, webhook operations, Next security config.
- Operations: `.dockerignore`, Prometheus alerts, architecture, phase-2 operations, incident and observability runbooks.
- Tests: core, identity, phase 2, resilience, PostgreSQL concurrency/RLS, onboarding/approval, frontend contracts, payment correctness, and Safaricom contracts.

## 10. Tests and validation run

| Command/check | Result |
|---|---|
| `ruff check app tests alembic` | Passed |
| `ruff format --check app tests alembic` | Passed, 56 files formatted |
| `python3 -m compileall -q app alembic tests` | Passed |
| `git diff --check` | Passed |
| `alembic heads` | Passed; one head at `0012_v2_ledger_coupling` |
| SQLite `alembic upgrade head` from an empty database | Passed after migration portability fixes |
| Full Docker/PostgreSQL test suite | **106 passed, 4 skipped, 1 warning in 53.86s** |
| PostgreSQL role provisioning/migration/grants drill | Passed |
| `npm run lint` | Passed |
| `npx tsc --noEmit` | Passed |
| `npx vitest run --pool=threads --maxWorkers=1` | **5 files, 12 tests passed** |
| `npm run build` | Passed; 20 routes generated |
| Dashboard Docker production build | Passed |
| `bandit -q -r app` | Passed |
| `pip-audit -r requirements.txt` | Passed; no known vulnerabilities |
| `npm audit --audit-level=high` | Passed; zero vulnerabilities |
| Production compose interpolation/config validation | Passed with non-secret validation placeholders |
| Local current-artifact HTTP read probe | **500 requests, 0 failures, concurrency 20, 148.3 rps, 127.1 ms mean, 202.8 ms p95** |

The read probe exercised an authenticated payment-list route against the rebuilt local Docker API/PostgreSQL stack. It is bounded smoke evidence only: it does not represent STK/provider capacity, write throughput, multi-node behavior, or a sustained soak.

## 11. Skips, failed attempts, and warnings

- Four Safaricom contract tests were skipped because dedicated sandbox credentials and explicit live-STK test consent were not configured. They are double-gated by `RUN_DARAJA_SANDBOX_TESTS=1` and `RUN_DARAJA_SANDBOX_STK_TESTS=1`.
- The first local Next/Turbopack build failed because the execution sandbox prohibited an internal port bind; the identical build passed with the required local build permission and also passed inside Docker.
- Initial Python/npm advisory queries failed restricted DNS; both passed when rerun with advisory-network access.
- SQLite migrations initially exposed unnamed/non-batch foreign-key defects; those defects were fixed and a new empty database migrated through head.
- An experimental in-process load mode hung in the local TestClient stack and was removed. The original external HTTP probe then passed against the rebuilt Docker artifact.
- Independent worker-mode smoke runs were intentionally not executed against the populated development queue because they could send webhooks/emails or contact Daraja.
- The only full-suite warning is Passlib importing Python's deprecated `crypt` module. Replace/upgrade that password-hashing dependency before Python 3.13.

No unresolved automated test failure is being hidden.

## 12. Safaricom contract status

Executable opt-in contracts cover OAuth, PayBill KES 1 STK, Till/Buy Goods KES 1 STK, status query, and callback success/failure behavior. They have **not been executed in this environment**. No production credentials were used.

Before any live pilot, record dedicated Safaricom sandbox evidence for both PayBill and Till, validate the final public callback URL/proxy chain, and classify all observed provider response/result codes.

## 13. Remaining risks and required external gates

1. Run controlled PayBill and Till Safaricom sandbox contracts with dedicated credentials and preserve evidence.
2. Deploy final AWS KMS/IAM policies, perform a full rotation/rollback/restored-backup decrypt drill, and review CloudTrail.
3. Run separate worker processes against an isolated staging queue with controlled webhook, SMTP, and Daraja endpoints; verify SIGTERM and lease recovery.
4. Validate real SMTP delivery plus bounce/complaint/suppression handling and OTLP/paging delivery.
5. Run sustained PostgreSQL multi-node load, callback bursts, queue starvation, database interruption, worker crash, backup/restore, and disaster-recovery exercises.
6. Expand database-enforced isolation around identity/bootstrap tables or introduce reviewed identity-specific lookup functions.
7. Replace inline CSP allowances with nonces; complete assistive-technology and dashboard security testing.
8. Complete independent penetration testing and dependency/SBOM/container-image review in the deployment environment.
9. Execute SmartLynxPOS cutover merchant by merchant with no dual initiation or ambiguous per-request fallback.

## 14. Conservative readiness decision

| Scope | Decision | Reason |
|---|---|---|
| Internal sandbox | **Go** | Repository tests, builds, PostgreSQL concurrency/RLS, migration, role, security, and bounded HTTP checks pass. |
| 10 live pilot merchants | **Conditional Go** | Proceed only after Safaricom PayBill/Till contracts, real KMS rotation, isolated worker/SMTP/OTLP smoke tests, backup/restore, and a focused security review pass. Use supervised onboarding and daily reconciliation review. |
| 100 merchants | **No-Go** | Requires completed live-pilot evidence, sustained/multi-node load and failure tests, operational staffing/on-call evidence, complete identity isolation hardening, and independent penetration/accessibility results. |
| 1,000 businesses | **No-Go** | Requires proven capacity planning, partition/archive strategy, production HA/DR, multi-node queue behavior, long-duration merchant operations, support tooling, and measured scaling evidence. |

## 15. Final CTO recommendation

Keep the product Daraja-only and launch in layers. Use the current build for internal sandbox/staging immediately. Do not onboard live merchants merely because repository tests pass. Complete the external provider, KMS, worker, communications, restore, and security gates; then admit at most 10 supervised merchants with explicit incident ownership, daily unknown/unmatched review, and no SmartLynxPOS dual initiation. Treat 100 and 1,000 merchants as later evidence-based gates, not calendar targets.

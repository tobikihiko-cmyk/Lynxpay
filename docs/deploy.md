# LynxPay Render deployment guide

This guide deploys LynxPay to Render using `render.yaml`. Deploy staging first,
complete the Safaricom sandbox journey, and retain the resulting test evidence
before creating production services.

## Deployed architecture

The Blueprint provisions:

- PostgreSQL 16 with high availability, storage autoscaling, private networking,
  and PgBouncer;
- persistent Render Key Value with `noeviction`;
- two or more FastAPI instances behind `/ready`;
- two or more Next.js dashboard instances;
- separate webhook, reconciliation, reversal, email, and maintenance workers;
- automatic Alembic migrations and runtime grants in the API pre-deploy phase;
- health-gated, zero-downtime Render deploys and service autoscaling.

The API, workers, dashboard, PostgreSQL, and Key Value service use the Frankfurt
region. Change all services together if a different region is selected.

## 1. Release prerequisites

Before deploying a commit:

```bash
ruff check app tests alembic ops/release_evidence.py
ruff format --check app tests alembic ops/release_evidence.py
pytest -q
cd apps/merchant-dashboard
npm run lint
npx tsc --noEmit
npm test
```

The GitHub `LynxPay CI` workflow also runs PostgreSQL concurrency tests and the
Docker-backed Playwright merchant journey. A tag matching `v*`, or a manual
`LynxPay signed release` run, publishes API and dashboard images to GHCR by
commit SHA, generates SBOMs, signs both digests with Sigstore, adds GitHub
provenance attestations, and uploads release evidence.

Do not deploy a release whose quality or browser job failed.

## 2. Create the Render Blueprint

1. Connect Render to the `kihiko-jay/Lynxpay` GitHub repository.
2. Create a Blueprint from `render.yaml`.
3. Review the plan, region, database HA, PgBouncer, service counts, and costs.
4. Leave automatic deployment set to `checksPass`.
5. Supply every environment variable marked `sync: false`.

The Blueprint conforms to Render's official schema. The first API pre-deploy
will fail safely until database roles and role-specific URLs are configured.
This is expected during bootstrap; no application traffic should exist yet.

## 3. Bootstrap PostgreSQL roles

LynxPay does not run production traffic as the Render database owner. It uses:

| Role | Purpose |
| --- | --- |
| `lynxpay_migrator` | Alembic and post-migration grants |
| `lynxpay_api` | Tenant-scoped API reads and writes |
| `lynxpay_worker` | Explicit operational cross-tenant worker policies |
| `lynxpay_admin` | Platform administration through MFA-protected endpoints |
| `lynxpay_metrics` | Read-only operational metrics |
| `lynxpay_readonly` | Approved support/read-only access |

From a trusted Render shell or temporarily allowlisted administrator host, run
the provisioning script using the initial database owner URL:

```bash
psql "$BOOTSTRAP_DATABASE_URL" \
  -v database_name=lynxpay \
  -v migrator_password="$LYNXPAY_MIGRATOR_PASSWORD" \
  -v api_password="$LYNXPAY_API_PASSWORD" \
  -v worker_password="$LYNXPAY_WORKER_PASSWORD" \
  -v admin_password="$LYNXPAY_ADMIN_PASSWORD" \
  -v metrics_password="$LYNXPAY_METRICS_PASSWORD" \
  -v readonly_password="$LYNXPAY_READONLY_PASSWORD" \
  -f ops/provision-postgres-roles.sql
```

Generate each password independently with at least 32 random bytes. Do not put
passwords in source control, shell transcripts, tickets, or release evidence.
Remove any temporary external database allowlist immediately after bootstrap.

Construct six private PgBouncer connection URLs using the corresponding role
and password. Configure:

```text
MIGRATION_DATABASE_URL=<lynxpay_migrator pooled URL>
DATABASE_URL=<lynxpay_api pooled URL>
WORKER_DATABASE_URL=<lynxpay_worker pooled URL>
ADMIN_DATABASE_URL=<lynxpay_admin pooled URL>
METRICS_DATABASE_URL=<lynxpay_metrics pooled URL>
```

`DATABASE_URL`, `ADMIN_DATABASE_URL`, and `METRICS_DATABASE_URL` belong on the
API. Workers need `DATABASE_URL` and `WORKER_DATABASE_URL`. Never give the
migrator, admin, or metrics URL to a worker.

Redeploy the API. Its pre-deploy command runs:

```bash
DATABASE_URL="$MIGRATION_DATABASE_URL" alembic upgrade head
psql "$MIGRATION_DATABASE_URL" -f /app/ops/apply-runtime-grants.sql
```

Only one service owns migrations, so multiple API/worker replicas cannot race
Alembic.

## 4. Configure shared secrets

Generate secrets outside Render:

```bash
openssl rand -hex 32
openssl rand -base64 32
```

Configure the same values on the API and every worker:

```text
SECRET_KEY=<shared JWT signing secret>
ENCRYPTION_ACTIVE_KEY_ID=v1
ENCRYPTION_KEYS_JSON={"v1":"<shared random envelope key>"}
```

The Blueprint generates the API secret initially, but workers use manually
synchronized values. Replace the generated value with one controlled secret and
verify exact equality across services before processing jobs.

For production, prefer:

```text
ENCRYPTION_PROVIDER=aws_kms
ENCRYPTION_KMS_KEY_IDS_JSON={"v1":"<approved KMS key ARN>"}
AWS_REGION=<KMS region>
```

Apply `ops/aws-kms-runtime-policy.json` with real account, role, and key ARNs.
Do not use local envelope keys for the final production launch.

## 5. Configure URLs, SMTP, and callbacks

Set these API values after Render assigns service URLs:

```text
PUBLIC_BASE_URL=https://<api-host>
DASHBOARD_PUBLIC_URL=https://<dashboard-host>
ALLOWED_ORIGINS=https://<dashboard-host>
MPESA_CALLBACK_VERIFY_MODE=ip_allowlist
MPESA_CALLBACK_IP_ALLOWLIST=<current approved Safaricom networks>
```

Render also supplies `RENDER_EXTERNAL_URL` and `RENDER_GIT_COMMIT`; `/health`
reports the deployed commit SHA. `PUBLIC_BASE_URL` remains the explicit
production source of callback URLs.

Configure the API and email worker with the same SMTP settings:

```text
EMAIL_DELIVERY_MODE=smtp
SMTP_HOST=<provider host>
SMTP_PORT=587
SMTP_USERNAME=<secret>
SMTP_PASSWORD=<secret>
SMTP_FROM_EMAIL=<verified sender>
SMTP_STARTTLS=true
```

Verify SPF, DKIM, DMARC, bounce handling, complaint handling, and password-reset
delivery before onboarding external users.

## 6. Configure monitoring

Set a random `METRICS_BEARER_TOKEN`, expose `/metrics` only to the approved
collector, and load:

- `ops/prometheus.yml`;
- `ops/prometheus-alerts.yml`;
- `ops/grafana/lynxpay-overview.json`.

Configure `OTEL_EXPORTER_OTLP_ENDPOINT` for the approved trace collector. Traces
and logs must not contain phone numbers, credentials, raw callback bodies,
authorization headers, API keys, or decrypted email payloads.

Follow `docs/runbooks/observability.md` and
`docs/runbooks/escalation-policy.md` before enabling paging.

## 7. Verify the deployment

Check:

```bash
curl -fsS https://<api-host>/health
curl -fsS https://<api-host>/ready
curl -fsS https://<dashboard-host>/sign-in
```

Expected API responses include:

```json
{"status":"ok","service":"lynxpay","version":"0.1.0","release_sha":"<commit>"}
{"status":"ready"}
```

Confirm each worker mode reports a fresh heartbeat and that PostgreSQL is at:

```text
0017_payment_correlation
```

Then complete the staging merchant journey:

1. Register and verify a new account.
2. Enrol MFA and revoke a secondary session.
3. Complete business onboarding.
4. Add sandbox Daraja credentials and test OAuth.
5. Complete the callback-confirmed KES 1 verification.
6. Add catalog items and create an invoice.
7. Open its public link and complete sandbox payment.
8. Complete one walk-in payment.
9. Confirm invoice, receipt evidence, reports, and CSV export.
10. Configure a webhook and verify signature, retry, and replay.
11. Generate and revoke an API key.
12. Request and independently approve a sandbox reversal.

Local internet access alone cannot receive Safaricom callbacks because
Safaricom cannot route to a laptop's `localhost`. Staging must use public HTTPS.

## 8. Load and failure evidence

Use `tests/failure/README.md`. At minimum retain evidence for:

- callback bursts and duplicate callbacks;
- concurrent sandbox STK initiation;
- Redis fail-closed behavior and recovery;
- worker crash and lease recovery;
- reconciliation backlog drain;
- PgBouncer saturation;
- deployment during active callbacks;
- multiple API and worker replicas.

Generated evidence belongs under `artifacts/` and is uploaded with the release;
it must not contain customer or credential data.

## 9. Rollout and rollback

Render keeps the old instances serving until new instances pass health checks.
For commit-bound deployment with post-deploy verification, configure private
deploy hooks and run:

```bash
RELEASE_SHA=<new commit> \
PREVIOUS_GOOD_SHA=<known-good commit> \
API_DEPLOY_HOOK_URL=<secret> \
DASHBOARD_DEPLOY_HOOK_URL=<secret> \
API_HEALTH_URL=https://<api-host>/health \
DASHBOARD_HEALTH_URL=https://<dashboard-host>/sign-in \
ops/render-deploy.sh
```

The script waits until `/health` reports the target commit, checks the dashboard,
and triggers the previous commit if the gate fails. Deploy hooks are secrets.

The release workflow also produces signed GHCR images by digest. Render's
Blueprint is Git-backed, so it rebuilds the same reviewed commit rather than
pulling those images. A stricter artifact-enforcement deployment can convert
services to Render image-backed services and trigger each deploy hook with the
signed `imgURL` digest. Retain old images because image-backed rollback requires
the previous digest to remain available.

Never automatically downgrade a database migration. Roll back application code
only when the previous version is compatible with the current migration head.

## 10. Production release gates

Do not accept live merchant traffic until all of these are recorded:

- approved Safaricom live credentials and callback/network contract evidence;
- successful backup restoration and KMS decrypt/rotation drill;
- SMTP and paging delivery from production;
- signed release, SBOM, image digest, exact commit, and migration evidence;
- sustained capacity, callback burst, worker failure, and database saturation results;
- penetration test and independent accessibility review;
- reviewed privacy, terms, retention, incident, and merchant-support procedures;
- named release owner, incident commander rotation, and rollback operator.

The first production rollout should be a small, observed merchant pilot. Expand
only after payment success, unknown-payment age, callbacks, reconciliation,
webhook delivery, and support workload remain within agreed thresholds.

# Phase 2 operations

## Processes

Run database migrations once, then run API and worker as separate processes:

```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.worker
```

`python -m app.worker --once` is useful for a scheduler or smoke test. PostgreSQL workers claim webhook and reconciliation rows with `FOR UPDATE SKIP LOCKED`, set expiring leases, and recover abandoned work.

Production also requires Redis-backed rate limiting. `/metrics` is protected by `METRICS_BEARER_TOKEN`; OpenTelemetry export is enabled only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Load `ops/prometheus-alerts.yml` and route it to an owned paging service before accepting traffic.

Identity email is queued with an encrypted payload. Set `EMAIL_DELIVERY_MODE=smtp` only with a configured SMTP host and run the worker. Failed deliveries use leases and bounded retries before dead-lettering. Password-reset responses intentionally do not disclose whether an account exists.

For local Compose validation without creating `.env`, use the non-secret example file explicitly:

```bash
LYNXPAY_ENV_FILE=.env.example docker compose --env-file .env.example up --build
```

If those ports are occupied, set `LYNXPAY_API_PORT` and `LYNXPAY_DASHBOARD_PORT` for the host bindings.

## PostgreSQL roles and RLS

RLS is only effective when the API connects as a non-owner role without `BYPASSRLS`. Use:

- a migration owner role for Alembic only;
- a non-owner API role for request traffic;
- a tightly controlled worker role with `BYPASSRLS`, supplied through `WORKER_DATABASE_URL`, because workers must lease due work across organizations.

Do not expose the worker connection string to the API container. Test cross-tenant reads and writes using the actual deployment roles in staging.

## Webhook receiver contract

Each POST includes:

- `X-LynxPay-Delivery-Id`;
- `X-LynxPay-Event`;
- `X-LynxPay-Signature: t=<unix>,v1=<hex-hmac>`.

Receivers must compute HMAC-SHA256 over the exact bytes `timestamp + "." + request_body`, reject stale timestamps, use constant-time comparison, and idempotently store the delivery ID. A 2xx response is success. Redirects are never followed. Retries eventually move to `dead_letter`; a replay is a new delivery with its own ID.

## Encryption rotation

For local envelope keys, set a keyring such as:

```text
ENCRYPTION_PROVIDER=local
ENCRYPTION_ACTIVE_KEY_ID=v2
ENCRYPTION_KEYS_JSON={"v1":"old-master","v2":"new-master"}
```

For AWS KMS, map versions to KMS keys:

```text
ENCRYPTION_PROVIDER=aws_kms
ENCRYPTION_ACTIVE_KEY_ID=v2
ENCRYPTION_KMS_KEY_IDS_JSON={"v1":"arn:aws:kms:...:key/old","v2":"arn:aws:kms:...:key/new"}
```

Deploy readers with both versions first, switch the active version, inspect the dry run, then run the audited re-encryption job:

```bash
python -m app.rotate_encryption
python -m app.rotate_encryption --apply
```

Verify no rows reference the old version, then retire it. Do not remove an old key while ciphertext still references it. The job requires the same cross-tenant database authority as the worker and must run as a controlled operation.

The job rotates Daraja credentials, webhook secrets, MFA seeds, and encrypted email payloads. Use the least-privilege policy template and ceremony in `docs/runbooks/kms-rotation.md`; account-specific ARNs must be reviewed and rendered before use.

## Controlled integration tests

Default tests never contact Safaricom or a PostgreSQL server. To run the concurrency test, provide a disposable database whose name contains `test`:

```bash
POSTGRES_TEST_DATABASE_URL=postgresql://.../lynxpay_test pytest -q tests/test_postgres_concurrency.py
```

To verify OAuth and an existing checkout status in Safaricom sandbox:

```bash
RUN_DARAJA_SANDBOX_TESTS=1 \
DARAJA_SANDBOX_CONSUMER_KEY=... \
DARAJA_SANDBOX_CONSUMER_SECRET=... \
DARAJA_SANDBOX_PASSKEY=... \
DARAJA_SANDBOX_SHORTCODE=... \
DARAJA_SANDBOX_CHECKOUT_REQUEST_ID=... \
pytest -q tests/test_safaricom_sandbox_contract.py
```

The suite deliberately does not initiate an STK Push. Use a dedicated sandbox account and an already-created checkout ID. Safaricom's official [Daraja portal](https://daraja.safaricom.co.ke/) is the authority for current sandbox credentials and API availability.

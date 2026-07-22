# LynxPay Deployment Guide

This guide deploys LynxPay to a public staging environment on Render. Use this
for Safaricom sandbox and callback testing first. Do not use it as a live
merchant production launch until the external gates in
`docs/v2-live-pilot-validation-2026-07-16.md` are complete.

## Target Architecture

- FastAPI LynxPay API as a Render Docker web service.
- Next.js merchant dashboard as a separate Render Docker web service.
- Managed Render PostgreSQL.
- Public HTTPS API URL for M-PESA callback delivery.

Workers, SMTP, Redis-backed rate limits, production database role separation,
and KMS-backed encryption should be added before production traffic.

## 1. Connect GitHub

1. Create or sign in to a Render account.
2. Connect GitHub.
3. Give Render access to `kihiko-jay/Lynxpay`.
4. Deploy from branch `main`.

## 2. Create PostgreSQL

1. Render Dashboard -> New -> Postgres.
2. Name: `lynxpay-postgres`.
3. Choose the same region you will use for the API and dashboard.
4. Prefer a paid always-on plan for callback testing.
5. Copy the internal database URL after creation.

## 3. Deploy API

1. Render Dashboard -> New -> Web Service.
2. Select repository `kihiko-jay/Lynxpay`.
3. Runtime: Docker.
4. Dockerfile path: `./Dockerfile`.
5. Health check path: `/ready`.
6. Start command:

```bash
/bin/sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"
```

Set these environment variables:

```text
ENVIRONMENT=staging
PROCESS_TYPE=api
DATABASE_URL=<Render internal Postgres URL>
SECRET_KEY=<openssl rand -hex 32>
SECRET_ENCRYPTION_KEY=<openssl rand -hex 32>
ENCRYPTION_PROVIDER=local
ENCRYPTION_ACTIVE_KEY_ID=v1
ENCRYPTION_KEYS_JSON={"v1":"<openssl rand -base64 32>"}
EMAIL_DELIVERY_MODE=outbox
METRICS_ENABLED=false
RATE_LIMIT_ENABLED=false
PUBLIC_BASE_URL=https://<api-service>.onrender.com
DASHBOARD_PUBLIC_URL=https://<dashboard-service>.onrender.com
ALLOWED_ORIGINS=https://<dashboard-service>.onrender.com
MPESA_CALLBACK_VERIFY_MODE=ip_allowlist
MPESA_CALLBACK_IP_ALLOWLIST=
```

For staging only, an empty `MPESA_CALLBACK_IP_ALLOWLIST` makes Safaricom
callback testing easier. Do not leave it empty in production.

## 4. Deploy Dashboard

1. Render Dashboard -> New -> Web Service.
2. Select repository `kihiko-jay/Lynxpay`.
3. Runtime: Docker.
4. Root directory or build context: `apps/merchant-dashboard`.
5. Dockerfile path: `apps/merchant-dashboard/Dockerfile`.
6. Set environment variables:

```text
NODE_ENV=production
LYNXPAY_API_URL=https://<api-service>.onrender.com
```

After the dashboard URL is known, update and redeploy the API with:

```text
DASHBOARD_PUBLIC_URL=https://<dashboard-service>.onrender.com
ALLOWED_ORIGINS=https://<dashboard-service>.onrender.com
```

## 5. Verify API

Open:

```text
https://<api-service>.onrender.com/health
https://<api-service>.onrender.com/ready
```

Expected responses:

```json
{"status":"ok","service":"lynxpay","version":"..."}
{"status":"ready"}
```

## 6. Verify Dashboard

Open:

```text
https://<dashboard-service>.onrender.com/sign-up
```

Create a staging test account. Email verification is queued to the outbox unless
SMTP is configured.

## 7. Test Daraja Sandbox

In the dashboard:

1. Complete the business profile.
2. Create a sandbox merchant.
3. Add sandbox Daraja credentials.
4. Test credentials.
5. Send the KES 1 verification STK Push.

The callback URL must be public HTTPS, for example:

```text
https://<api-service>.onrender.com/api/v1/callbacks/mpesa/<merchant_id>
```

This is why local `localhost` callback testing fails even when your machine has
internet access: Safaricom cannot call back to your laptop's `localhost`.

## 8. Before Production

Before live merchants, add and verify:

- Redis or Render Key Value with `RATE_LIMIT_ENABLED=true`.
- SMTP delivery, bounce handling, and email worker.
- Webhook, reconciliation, email, and maintenance workers.
- Production callback source validation.
- AWS KMS or equivalent managed key storage.
- Separate production database roles and grants.
- Metrics authentication and alerting.
- Backup/restore, worker failover, load, and Safaricom contract evidence.
- Independent security and accessibility review.

Use `docs/v2-live-pilot-validation-2026-07-16.md` as the release gate checklist.

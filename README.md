# LynxPay

LynxPay is a standalone M-PESA Daraja infrastructure service. Each organization supplies and owns its own PayBill/Till and Daraja credentials. LynxPay initiates STK Push requests, preserves callbacks, tracks payment state, writes audit/ledger events, reconciles ambiguous outcomes, and delivers signed merchant webhooks.

LynxPay is not a payment aggregator. It does not hold merchant funds, settle merchants, or share credentials between merchants.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Or use PostgreSQL and the API together:

```bash
cp .env.example .env
docker compose up --build
```

Generate independent production secrets:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Use one value for `SECRET_KEY` and the other for `SECRET_ENCRYPTION_KEY`.

## Initial flow

The dashboard provides a six-step onboarding wizard:

1. Create the organization owner account.
2. Complete the legal/business/contact profile.
3. Add the merchant's own PayBill, Till, or store-number configuration.
4. encrypt and test that merchant's Daraja credentials.
5. Send an administrator-only KES 1 verification STK Push and wait for a successful callback.
6. Activate the merchant and issue a merchant-bound, environment-specific API key shown once.

See the [merchant onboarding contract](docs/merchant-onboarding.md) for lifecycle and recovery behavior.

Swagger documentation is available at `/docs` outside production.

Run the delivery/reconciliation worker separately:

```bash
python -m app.worker
```

The merchant application is a separate Next.js runtime in `apps/merchant-dashboard/`. It uses a same-origin backend-for-frontend so access and refresh tokens remain in secure server-managed cookies:

```bash
cd apps/merchant-dashboard
cp .env.example .env.local
npm ci
npm run dev
```

## Verification

```bash
make verify
make security
make test-docker
```

See [architecture](docs/architecture.md), [merchant onboarding](docs/merchant-onboarding.md), [launch readiness](docs/launch-readiness-2026-07-15.md), [Phase 2 operations](docs/phase2-operations.md), the [validation record](docs/validation-2026-07-15.md), and the [SmartLynxPOS migration runbook](docs/smartlynxpos-migration.md).

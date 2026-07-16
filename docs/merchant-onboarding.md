# Merchant registration and onboarding

The Phase 1 dashboard implements a resumable six-step onboarding flow. LynxPay remains infrastructure rather than a payment aggregator: the organization supplies its own M-PESA account and Daraja credentials, and the KES 1 verification payment goes directly to that merchant account.

## Wizard lifecycle

1. **Create account** — creates the organization, owner user, and revocable session through `POST /api/v1/auth/register`.
2. **Business profile** — records legal name, business type, county, town, contact phone, and support email through tenant-scoped `GET/PATCH /api/v1/organization`.
3. **M-PESA setup** — records PayBill, Till, or store number, shortcode, environment, and the canonical callback URL. Till/store-number setups require the Till number.
4. **Daraja credentials** — encrypts the merchant's consumer key, consumer secret, and passkey, then verifies OAuth. Saving secrets moves the merchant to `credentials_added`; a successful test moves it to `verified`.
5. **Test payment** — a human owner/admin sends exactly KES 1 with payment purpose `merchant_verification`. The wizard remains on this step for `created`, `pending`, `stk_sent`, or `unknown`; it advances only after a valid callback makes the payment `success`.
6. **Activation** — the API verifies owner email, current legal consent, tested credentials, and a successful merchant-verification callback created after the latest credential test. Sandbox merchants can then activate directly. Production merchants enter `pending_approval` and require an independent LynxPay platform administrator. A merchant-bound API key can be issued only after activation; the full key is displayed once and only its digest is stored.

## Safety rules

- Normal payments require an `active` merchant; merchant-verification payments require a `verified` merchant.
- API keys cannot create merchant-verification payments, even with `payments:write`.
- A verification payment must be exactly KES 1 and is recorded in the ordinary payment, attempt, callback, ledger, audit, and reconciliation infrastructure.
- STK acceptance is not callback confirmation. The activation step requires `payment.status=success`.
- An `unknown` verification attempt must be reconciled; the wizard does not send a second prompt while provider acceptance is uncertain.
- Production selection displays a live-money warning. Sandbox and production credentials and API keys remain isolated.
- Daraja secrets and full API keys are never persisted by the browser wizard. A lost one-time API-key value must be rotated, not recovered.

## Recovery

The browser does not persist wizard state, bearer tokens, Daraja credentials, or API keys in local or session storage. On each load the Next.js backend-for-frontend reads the authenticated organization, merchant lifecycle, and latest `merchant_verification` payment from tenant-scoped APIs, then derives the first incomplete step. Credentials must be re-entered if their submission did not complete. Verification status is polled from the API and no callback or success result is inferred from browser state.

# How LynxPay Works

LynxPay is a standalone M-PESA Daraja infrastructure service. It helps a
business initiate STK Push payments, preserve callback evidence, track payment
state, reconcile ambiguous outcomes, and notify the merchant's systems through
signed webhooks.

LynxPay is not a payment aggregator. It does not pool funds, hold merchant
money, settle merchants, or share one PayBill/Till across many businesses. Each
merchant uses and owns its own Safaricom PayBill, Till, store number, and Daraja
credentials.

## 1. The Core Model

LynxPay has three major planes:

- **Control plane:** user accounts, organization profile, onboarding, team
  roles, MFA, API keys, merchant approval, and audit logs.
- **Payment plane:** STK Push requests, payment attempts, M-PESA callbacks,
  status checks, reconciliation, ledger entries, and payment state.
- **Delivery plane:** signed merchant webhooks, retries, dead letters, replay,
  and endpoint health.

The most important rule is simple:

```text
Customer pays -> Safaricom M-PESA -> merchant's own PayBill/Till account
```

LynxPay records and orchestrates the payment flow, but the money settles through
Safaricom directly to the merchant's own settlement path.

## 2. Merchant Onboarding

Onboarding starts in the merchant dashboard.

### Step 1: Create Owner Account

The business owner creates a LynxPay account with:

- organization name,
- owner name,
- work email,
- Kenyan mobile number,
- password.

LynxPay creates:

- an organization,
- an owner user,
- an authentication session,
- an email verification token,
- audit evidence that the organization was created.

Email verification is required before sensitive production actions are approved.

### Step 2: Complete Business Profile

The merchant fills in legal and support details:

- registered legal name,
- business type,
- county,
- town,
- business phone,
- support email.

This gives LynxPay enough context to identify the business operating each
M-PESA merchant account and to support later review, suspension, or incident
workflows.

### Step 3: Connect PayBill or Till

The merchant creates a LynxPay merchant account and chooses:

- PayBill,
- Buy Goods Till,
- store number,
- sandbox or production environment.

For local and staging deployments, LynxPay generates a callback URL based on
`PUBLIC_BASE_URL`, for example:

```text
https://api.example.com/api/v1/callbacks/mpesa/<merchant_id>
```

This callback URL is what Safaricom uses to tell LynxPay whether the STK Push
succeeded, failed, timed out, or returned another result.

For real Daraja testing, the callback URL must be public HTTPS. A local
`localhost` URL cannot receive callbacks from Safaricom.

### Step 4: Store Daraja Credentials

The merchant enters their own Daraja credentials:

- consumer key,
- consumer secret,
- Lipa na M-PESA passkey,
- shortcode,
- environment.

LynxPay encrypts sensitive values before storing them. Credentials are masked in
dashboard responses and are not returned in plaintext after creation.

The merchant then runs a credential test. LynxPay performs a Daraja OAuth
handshake to prove the credentials are usable for the selected environment.

### Step 5: KES 1 Verification Payment

Before activation, LynxPay sends a controlled KES 1 STK Push to prove the whole
payment path works:

1. LynxPay creates a durable payment record.
2. LynxPay creates an initial payment attempt.
3. LynxPay calls Safaricom Daraja STK Push.
4. Safaricom sends an STK prompt to the test phone.
5. The customer/tester approves the prompt.
6. Safaricom sends a callback to LynxPay.
7. LynxPay validates and stores the callback.
8. LynxPay marks the verification payment successful if evidence matches.

This proves:

- outbound Daraja credentials work,
- Safaricom accepts the STK request,
- the customer phone receives the prompt,
- the callback URL is reachable,
- LynxPay can extract the receipt,
- payment state transitions are working.

### Step 6: Activation or Production Approval

For sandbox merchants, the organization owner can activate after the onboarding
gates pass.

For production merchants, LynxPay requires independent platform approval. A
production merchant cannot self-approve. Approval checks include:

- current terms accepted,
- current privacy version accepted,
- verified organization owner,
- active tested production credentials,
- successful KES 1 verification payment after credential testing,
- platform-admin review reason and audit trail.

Once approved, the merchant can become active and issue API keys.

## 3. API Keys and Integration

After a merchant is active, LynxPay can issue an API key. The key is shown once.
Only the hash is stored.

API keys can be scoped, for example:

```text
payments:read
payments:write
callbacks:read
webhooks:read
webhooks:write
```

Production payment-write keys are merchant-bound. This prevents one key from
initiating payments for unrelated merchants or environments.

A merchant server initiates STK Push with a request like:

```bash
curl -X POST https://<lynxpay-api>/api/v1/payments/stk-push \
  -H "X-API-Key: <YOUR_API_KEY>" \
  -H "Idempotency-Key: order-1001" \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_id": "<merchant_id>",
    "amount": 100,
    "phone_number": "254712345678",
    "external_reference": "ORDER-1001",
    "description": "Order payment"
  }'
```

The `Idempotency-Key` protects the merchant from accidentally creating duplicate
payments when retrying the same request.

## 4. What Happens During STK Push

When LynxPay receives an STK Push request, it does not call Daraja first.
Instead, it stores durable internal evidence first.

The order is:

1. Validate the API key, scope, merchant binding, and environment.
2. Verify the merchant is active.
3. Check `external_reference` and `Idempotency-Key`.
4. Create a `Payment` row.
5. Write a payment ledger entry.
6. Write an audit entry.
7. Transition the payment to `pending`.
8. Create a `PaymentAttempt`.
9. Commit the database transaction.
10. Call Daraja STK Push.

This is important. If the process crashes before calling Daraja, LynxPay has
evidence that the attempt existed and can recover safely. If the process crashes
after the provider call, LynxPay can mark the submission uncertain and require
reconciliation or review rather than silently losing the payment.

## 5. Payment States

LynxPay separates different kinds of payment uncertainty.

Common states include:

- `created`: payment row exists.
- `pending`: LynxPay is preparing/submitting the request.
- `stk_sent`: Daraja accepted the STK Push request.
- `success`: verified callback or status-query evidence confirms payment.
- `failed`: Daraja or callback evidence confirms failure.
- `timeout`: customer did not complete in time.
- `unknown`: LynxPay cannot safely prove success or failure yet.

LynxPay does not treat STK acceptance as payment success. STK acceptance only
means Safaricom accepted the prompt request. Payment success requires callback or
status-query evidence.

## 6. Callback Processing

Safaricom sends payment results to the callback URL.

LynxPay handles callbacks with a raw-first rule:

1. Read and size-limit the raw callback body.
2. Store the raw callback evidence.
3. Commit the callback before mutating payment state.
4. Check source or signature rules.
5. Parse the M-PESA result fields.
6. Match the callback to a payment attempt or CheckoutRequestID.
7. Validate amount, phone, receipt, and merchant evidence.
8. Transition the payment only when evidence is safe.
9. Queue merchant webhooks.

This means even malformed, rejected, duplicate, or unmatched callbacks are still
preserved for investigation.

### Success Callback

For a success result, LynxPay expects evidence such as:

- CheckoutRequestID,
- MerchantRequestID,
- M-PESA receipt number,
- amount,
- customer phone,
- result code `0`.

If evidence matches the payment, LynxPay marks the payment `success`, records
the receipt, writes ledger/audit entries, and queues a `payment.success` webhook.

### Failure Callback

For a failure result, LynxPay records:

- result code,
- result description,
- provider category,
- failure or timeout state.

It then marks the payment `failed`, `timeout`, or `unknown` depending on the
provider code and current payment state.

### Duplicate and Conflicting Callbacks

Safaricom or network retries may deliver duplicate callbacks. LynxPay detects
duplicates and does not double-count the payment.

If a callback contains conflicting success evidence, such as a different receipt
for a payment already marked successful, LynxPay does not overwrite the original
success silently. It marks the record for review.

## 7. Reconciliation

Sometimes callbacks are delayed, missing, malformed, or ambiguous. LynxPay uses
Daraja status queries to reconcile those payments.

Reconciliation is used for payments in states such as:

- `stk_sent`,
- `unknown`,
- needs review.

The reconciliation worker:

1. Claims eligible payments with a database lease.
2. Snapshots the provider request evidence.
3. Releases the payment row lock before calling Daraja.
4. Calls Daraja transaction-status query.
5. Reacquires the payment row.
6. Applies the result only if evidence still matches.
7. Lets callback-confirmed success win if it arrived during the query.
8. Writes a `PaymentStatusCheck`.
9. Updates review state and next reconciliation time.

This avoids holding database locks during provider network calls and prevents
late reconciliation from overwriting stronger callback evidence.

## 8. Safe Retry

LynxPay supports retries, but only when evidence makes retry safe.

A failed payment can be retried when Daraja definitely rejected the request or
LynxPay knows the request was not sent.

Timeout or unknown payments require explicit operator/admin override because
the customer may still have paid. LynxPay blocks retry if successful receipt
evidence exists.

Retry creates a new payment attempt under the same payment record and preserves
the previous CheckoutRequestID for audit/review.

## 9. Webhooks to Merchant Systems

Merchants can create webhook endpoints for payment events.

LynxPay sends signed webhook payloads for events such as:

- `payment.stk_sent`,
- `payment.success`,
- `payment.failed`,
- `payment.timeout`,
- `payment.unknown`,
- `webhook.test`.

Webhook delivery includes:

- HMAC signature,
- event ID,
- delivery ID,
- retry attempts,
- exponential backoff,
- response capture,
- dead-lettering,
- replay,
- endpoint auto-pause after repeated failures.

Webhook URLs are validated to reduce SSRF risk. In production, webhook URLs must
use HTTPS.

## 10. Where the Money Goes

This is the most important business point.

LynxPay does not collect the merchant's funds.

When the customer approves an STK Push:

1. The customer pays through M-PESA.
2. Safaricom credits the merchant's own PayBill/Till/settlement setup.
3. LynxPay receives callback evidence.
4. LynxPay records the payment state and receipt.
5. LynxPay notifies the merchant's system.

Funds do not pass through a LynxPay bank account or pooled wallet.

LynxPay provides infrastructure, evidence, control, auditability, and automation
around the merchant's own Daraja account.

## 11. Dashboard Views

The dashboard gives operators access to:

- onboarding progress,
- payment list and payment detail,
- payment attempts and timeline,
- callbacks,
- reconciliation issues,
- API keys,
- webhook endpoints and deliveries,
- audit logs,
- team management,
- platform-admin merchant approval and suspension.

The dashboard uses a backend-for-frontend pattern. Browser sessions are stored
in HttpOnly cookies instead of exposing access and refresh tokens to JavaScript.

## 12. Security and Audit Controls

LynxPay includes:

- hashed API keys,
- hashed refresh tokens,
- hashed reset and invitation tokens,
- encrypted Daraja credentials,
- append-only payment ledger,
- audit logs,
- role-based scopes,
- MFA support,
- tenant scoping,
- production runtime configuration validation,
- webhook SSRF controls,
- callback body limits,
- rate-limit support,
- separate production database-role design.

The goal is to make payment evidence durable, reviewable, and hard to tamper
with.

## 13. Local vs Public Testing

Local testing is good for:

- dashboard UI,
- account registration,
- API behavior,
- migrations,
- unit tests,
- basic onboarding flow.

Local testing is not enough for full Daraja verification because Safaricom
cannot call back to:

```text
http://localhost:8000
```

For KES 1 verification, use a public HTTPS staging URL or a tunnel such as
ngrok. In staging, `PUBLIC_BASE_URL` must point to the public API URL so the
generated callback URL is reachable by Safaricom.

## 14. End-to-End Flow Summary

The complete LynxPay flow looks like this:

```text
Merchant signs up
  -> verifies email
  -> completes business profile
  -> adds PayBill/Till
  -> encrypts Daraja credentials
  -> tests Daraja OAuth
  -> sends KES 1 verification STK
  -> receives Safaricom callback
  -> activates sandbox or submits production for approval
  -> issues scoped API key
  -> merchant server initiates customer STK Push
  -> customer approves M-PESA prompt
  -> funds settle to merchant's own Safaricom account
  -> LynxPay receives callback evidence
  -> LynxPay marks payment success/failure/unknown
  -> merchant system receives signed webhook
  -> operators review payments, callbacks, reconciliation, and audit logs
```

LynxPay is therefore the operational control plane around merchant-owned
M-PESA payments. It does not replace Safaricom settlement; it makes the Daraja
payment lifecycle safer, more observable, and easier to integrate.

## 15. Catalog and Invoice Collection

Each merchant owns a separate catalog. A barber does not inherit spa services,
and a tax consultant does not see law-firm services. A merchant may keep up to
20 active products or services, with editable names, descriptions, prices,
types, SKUs, and display order.

An invoice can be created in two ways:

- select catalog entries, quantities, and captured prices;
- enter a one-off service title, description, and amount without using catalog.

Catalog prices are copied into invoice line items. Editing the catalog later
does not rewrite an invoice that was already sent.

The invoice stores the merchant's public business name, address, support email,
and business phone. It does not expose owner credentials or private account
details. LynxPay creates an unguessable public payment URL that the merchant can
send by SMS, WhatsApp, email, or another channel.

When the client opens the link:

1. LynxPay shows the merchant, service or products, amount, and invoice status.
2. The client enters the Kenyan mobile number they want to pay from.
3. LynxPay creates one payment linked to the invoice and starts STK Push.
4. Duplicate clicks reuse the active payment instead of creating another prompt.
5. Verified M-PESA success changes the invoice to `paid` and displays receipt evidence.
6. A void, expired, or already-paid invoice cannot start a new payment.

## 16. Walk-In Collection

Walk-in mode is for barbers, salons, spas, clinics, repair counters, and similar
businesses where collecting a full client profile or sending an invoice adds
friction.

The operator selects a catalog service or enters a description and amount,
asks for the customer's M-PESA number, and starts STK Push immediately. LynxPay
still creates the same durable payment intention, attempt, callback, receipt,
ledger, audit, and reconciliation evidence. The difference is only the merchant
workflow: there is no invoice or long-lived customer record.

## 17. Reconciliation Reports

The reports view derives operational truth from payments and invoices; it is
not a general ledger or accounting package. Merchants can review and export:

- daily successful collections;
- pending, failed, timed-out, and unknown payments;
- invoice amount, status, payment, and receipt reconciliation;
- walk-in sales;
- M-PESA receipt, amount, timestamp, and reference evidence;
- CSV files for external reconciliation.

Exports contain merchant-scoped data and must be handled as financial evidence.

## 18. Reversals

A successful M-PESA payment can enter the controlled full-reversal workflow.
LynxPay does not silently or automatically reverse payments.

1. An MFA-authenticated owner or administrator requests reversal with a reason.
2. A different MFA-authenticated owner or administrator reviews and approves it.
3. A reversal worker acquires a durable lease and submits Safaricom's reversal request once.
4. Submission responses, result callbacks, timeout callbacks, audit events, and correlation IDs are retained.
5. Only a verified provider success callback changes the payment from `success` to `reversed`.
6. LynxPay appends reversal ledger evidence, queues `payment.reversed`, and reopens a paid invoice.

Unknown, failed, or timed-out reversals remain visible for operator and
Safaricom support review. Partial reversals are not currently supported.

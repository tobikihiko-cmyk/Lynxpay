# SmartLynxPOS to LynxPay migration

The cutover invariant is simple: one sale has exactly one STK initiator. SmartLynxPOS must never fall back to direct Daraja after sending the request to LynxPay, even when the response is ambiguous.

## Contract

For each sale, SmartLynxPOS sends:

- its immutable sale/payment ID as `external_reference`;
- a stable `Idempotency-Key` reused for every retry of that sale;
- the configured LynxPay `merchant_id`;
- amount, normalized customer phone input, and description.

SmartLynxPOS stores the returned LynxPay payment ID and treats `created`, `pending`, `stk_sent`, and `unknown` as unpaid. Only `success` finalizes the sale. It learns status through a signed idempotent webhook and/or `GET /api/v1/payments/{payment_id}` polling.

## Merchant-by-merchant sequence

1. Inventory the merchant's shortcode type, Till/store number, environment, direct Daraja callbacks, and every in-flight POS payment.
2. Create one LynxPay organization/merchant record and enter that merchant's own credentials. Never copy them to another merchant.
3. Complete sandbox credential, STK, callback, reconciliation, and webhook contract tests.
4. Deploy POS code with a per-merchant routing flag, but leave that flag on `direct`.
5. At cutover, briefly stop new payment initiation for that merchant. Let every direct-Daraja checkout reach a callback or explicit reconciled/unknown state on the old path.
6. Change the merchant routing flag atomically to `lynxpay`, then resume new payments. From this point, all retries for a LynxPay-routed sale reuse its original idempotency key and remain on LynxPay.
7. Compare POS sales, LynxPay ledger entries, callbacks, and receipts during a defined observation window. Do not infer success from STK acceptance.
8. Remove the merchant's direct-Daraja initiation permission from POS after acceptance criteria pass. Keep old callback handling read-only long enough to drain delayed legacy callbacks.
9. Repeat for the next merchant.

## Rollback

Rollback is a new cutover boundary, not per-request fallback. Pause new initiation, drain/reconcile all LynxPay-routed in-flight sales, then switch only future sales back to direct mode. A sale already assigned a LynxPay payment ID must never be re-initiated directly.

## Suggested webhook mapping

| LynxPay event | POS action |
|---|---|
| `payment.stk_sent` | Keep sale awaiting payment. |
| `payment.success` | Idempotently finalize sale using LynxPay payment ID and receipt when present. |
| `payment.failed` / `payment.timeout` | Mark attempt failed; a new customer retry must create a new POS payment identity/reference. |
| `payment.unknown` | Hold for reconciliation/manual review; do not finalize or automatically initiate again. |
| `payment.reversed` | Enter an explicit audited reversal workflow. |

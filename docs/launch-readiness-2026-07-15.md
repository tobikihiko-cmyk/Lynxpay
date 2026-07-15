# LynxPay launch-readiness review — 2026-07-15

## Executive decision

**Live production merchants: no-go.**  
**Controlled sandbox/internal pilot: conditional go.**

The critical payment and callback correctness findings from the initial 6.8/10 review are resolved and covered by automated tests. LynxPay is now a credible pilot-grade payment-infrastructure core, but external Safaricom, AWS KMS, SMTP/telemetry, sustained-load, disaster-recovery, penetration, accessibility, and real SmartLynxPOS cutover evidence is still missing. Those are release gates, not documentation tasks.

## Current scorecard

| Area | Score | Assessment |
|---|---:|---|
| Product architecture | 8.5/10 | Standalone, tenant-oriented, merchant-owned credentials, no custody or aggregation. |
| Payment state machine | 8.5/10 | Definite rejection becomes failed; uncertain acceptance becomes unknown; success still requires verified evidence. |
| Daraja integration | 7.5/10 | Strong request/evidence boundaries, but controlled live sandbox contracts and token caching remain. |
| Security baseline | 8.0/10 | Encrypted secrets, hashed capabilities, environment keys, MFA replay defense, limits, audit/ledger DB protection. Live KMS/IAM drill remains. |
| Webhook infrastructure | 8.0/10 | Durable signed retries, leases, dead letters, replay, event IDs, SSRF controls, endpoint pause/listing. Controlled HTTPS contract remains. |
| Tenant isolation | 7.5/10 | Application scoping, merchant binding, environment predicates, payment-plane RLS, production-style role tests. Control-plane RLS is incomplete. |
| Dashboard/onboarding | 7.4/10 | Resumable six-step registration, business profile, Till/PayBill setup, credentials, KES 1 callback proof, activation, key handoff, and live warning. BFF cookies and several admin views remain. |
| Testing/release hygiene | 8.3/10 | 67 passed/2 external skips, PostgreSQL concurrency/RLS, migrations, onboarding contracts, lint/format/Bandit/dependency audit, Docker runner. CI hosting and external contracts remain. |
| Production readiness | 6.5/10 | Suitable for controlled sandbox/internal use after environment review; not approved for live merchant traffic. |
| **Overall** | **7.8/10** | Material improvement from 6.8/10; remaining risks are mainly external-system and operational proof. |

Scores reflect repository evidence available on this date and are not a certification.

## Closed launch-critical findings

- STK request failures no longer strand ordinary payments in `pending`; definite and uncertain outcomes are explicit, durable, audited, and webhook-ready.
- Callback idempotency is evidence-aware. Invalid success evidence cannot block a later valid callback, and different receipts become conflicts.
- Callback bodies are bounded and separately rate-limited; every accepted/rejected callback retains classified evidence.
- Test/live API keys are enforced by environment, and production payment-write keys require merchant binding.
- Credentials require human-admin JWT control; API keys cannot receive credential-write scope.
- Merchant activation requires saved and successfully tested credentials; Till/store-number configuration requires a Till number.
- Audit and payment-ledger rows have PostgreSQL update/delete trigger protection and restricted grants.
- Platform metrics have a separate database connection instead of assuming tenant-scoped visibility.
- Raw callback payload access requires `callbacks:read_raw`.
- The dashboard supports registration and same-origin merchant onboarding with live-mode warnings.
- A clean Docker test runner, formatter, security scanner, and dependency audit command are part of the repository.

## Remaining release gates

1. Run dedicated Safaricom sandbox OAuth, STK, callback, and transaction-status contracts, including known CheckoutRequestIDs and Till/PayBill variants.
2. Deploy final least-privilege AWS IAM/KMS policies, execute full key rotation and rollback, confirm CloudTrail evidence, and decrypt a restored backup.
3. Use production SMTP and OTLP/paging integrations; verify bounce/complaint handling and incident alerts end to end.
4. Extend database-enforced isolation to identity/control-plane tables or isolate bootstrap lookup functions behind a reviewed role/schema boundary.
5. Replace browser bearer-token storage with a same-origin BFF or secure cookie/CSRF architecture.
6. Run sustained soak/capacity, multi-node race, failure-injection, current-head backup/restore, regional recovery, and webhook HTTPS/SNI tests.
7. Complete independent penetration testing and assistive-technology accessibility review.
8. Execute the merchant-by-merchant SmartLynxPOS cutover with inventory, operator approvals, drain criteria, observability, and rollback rehearsals—never per-request fallback or dual STK initiation.

## Pilot conditions

A sandbox/internal pilot may proceed only with synthetic/non-production merchants, dedicated credentials, monitored callbacks/reconciliation, no merchant-money custody, and an explicit operator review of every `unknown` or callback `conflict`. Live production traffic requires all applicable release gates above to be closed and signed off.

# Data retention and archival

LynxPay treats payment, callback, ledger, and audit evidence as business records—not disposable application logs. Defaults are deliberately conservative:

- raw M-PESA callbacks and status checks: 400 days;
- delivered/dead-letter webhook delivery detail: 180 days;
- payment ledger and security audit evidence: seven years;
- payments and receipts: no automatic deletion.

`app.maintenance.retention_candidates` reports rows beyond the configured windows. It does not delete them. `RETENTION_DELETION_ENABLED` defaults to `false` and is not, by itself, an authorization to remove evidence. Before deletion is implemented, operations must provide an encrypted archive target, tenant/legal-hold exclusions, archive checksums, restore tests, approval audit records, and a merchant-facing retention policy.

Partition detach and encrypted cold-storage export are preferred for high-volume PostgreSQL tables. Raw callbacks must remain searchable during the dispute window, and ledger/audit records must remain append-only in hot or archived storage.

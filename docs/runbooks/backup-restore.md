# PostgreSQL backup and restore runbook

Use encrypted, access-controlled storage and a dedicated backup role. Backups contain callback payloads, customer data, hashed tokens, and encrypted credentials; they remain sensitive.

## Backup

```bash
pg_dump --format=custom --no-owner --no-acl "$DATABASE_URL" --file lynxpay.dump
sha256sum lynxpay.dump > lynxpay.dump.sha256
```

Record database version, Alembic version, application version, timestamp, checksum, encryption/storage location, and operator. Never place a dump in the repository.

## Restore drill

1. Provision an isolated empty PostgreSQL database with no production network egress.
2. Verify the checksum and restore with `pg_restore --exit-on-error --no-owner --no-acl`.
3. Run `alembic upgrade head` and integrity queries for organizations, merchants, payments, callbacks, ledger, audits, webhook attempts, sessions, and encrypted secrets.
4. Verify ciphertext decryptability using a controlled KMS/key context without printing plaintext.
5. Run tenant-isolation and callback concurrency tests against the restored database.
6. Destroy the isolated restore and its keys after recording RPO/RTO evidence.

Backups are not considered valid until a restore drill succeeds.

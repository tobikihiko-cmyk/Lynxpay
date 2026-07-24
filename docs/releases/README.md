# Release evidence

The release workflow generates one JSON manifest and one Markdown summary named
with the exact Git commit SHA. Evidence includes the Alembic head, test artifact
hashes, signed image digests, SBOM hashes, supported operations, known
limitations, environment variable names, and rollback inputs.

Generated evidence is uploaded with the GitHub release workflow under the
`lynxpay-release-evidence-<sha>` artifact. Run the same generator locally with:

```bash
python3 ops/release_evidence.py \
  --test-result artifacts/test/backend.xml \
  --test-result artifacts/test/dashboard.json \
  --api-image-digest sha256:<digest> \
  --dashboard-image-digest sha256:<digest>
```

Never add environment values, credentials, customer data, M-PESA receipts, or
raw callback bodies to release evidence.

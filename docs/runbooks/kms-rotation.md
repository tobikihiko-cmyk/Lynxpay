# AWS KMS envelope-key rotation

LynxPay calls only `kms:Encrypt`, `kms:Decrypt`, and `kms:DescribeKey`. KMS wraps random per-value data keys; merchant credentials, MFA seeds, webhook secrets, and email payloads are never sent to KMS as plaintext.

## IAM preparation

Render `ops/aws-kms-runtime-policy.json` with the real old/new key ARNs and the exact application key-version labels. Attach it to the runtime and controlled rotation roles. Do not grant wildcard KMS resources, `kms:*`, key administration, grants, or deletion/scheduling permissions to either role.

Each KMS key policy must allow those named roles to use the key, or delegate use authorization to IAM in the same account. Keep key-administrator and runtime roles separate. Require MFA and change approval for key administration. Validate the rendered policy with IAM Access Analyzer and the policy simulator before deployment.

## Rotation drill

1. Back up PostgreSQL and verify the restore in an isolated database.
2. Deploy readers configured with both key versions while the old version remains active.
3. Confirm CloudTrail data events and alerts cover denied/abnormal KMS use.
4. Create or select the new symmetric KMS key; do not repoint an alias in place as the only version record.
5. Change `ENCRYPTION_ACTIVE_KEY_ID` to the new label and keep both ARNs in `ENCRYPTION_KMS_KEY_IDS_JSON`.
6. Run `python -m app.rotate_encryption` and record the dry-run counts.
7. Run `python -m app.rotate_encryption --apply` using the controlled rotation role and worker database role.
8. Run the dry run again. Every category must report zero: Daraja credentials, webhook endpoints, MFA credentials, and email payloads.
9. Exercise credential decrypt, MFA login, queued email rendering, and webhook signing without logging plaintext.
10. Remove `kms:Encrypt` permission from the old key immediately. Retain old-key decrypt permission through the rollback/backup-retention window.
11. Restore the pre-rotation backup in isolation and prove the retained old key can decrypt it before scheduling key deletion.
12. After the approved retention window, remove old-key decrypt access, disable the old key, observe, and only then schedule deletion under dual control.

Abort if any ciphertext cannot decrypt, any dry-run count remains non-zero, audit rows are missing, CloudTrail is unavailable, or the restored backup cannot be read. Never delete the old key to force completion.

The repository cannot supply final account IDs, role ARNs, key ARNs, or change-approval evidence. Those values must come from the target AWS account and must not be invented.

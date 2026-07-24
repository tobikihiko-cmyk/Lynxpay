"""Generate immutable, secret-free LynxPay release evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any


def command(*parts: str) -> str:
    result = subprocess.run(parts, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def file_evidence(path: str) -> dict[str, Any]:
    artifact = Path(path)
    if not artifact.is_file():
        return {"path": path, "present": False}
    content = artifact.read_bytes()
    return {
        "path": path,
        "present": True,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def environment_names() -> list[str]:
    names: list[str] = []
    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#") and "=" in candidate:
            names.append(candidate.split("=", 1)[0])
    return sorted(set(names))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="artifacts/release")
    parser.add_argument("--test-result", action="append", default=[])
    parser.add_argument("--api-image-digest", default=os.getenv("API_IMAGE_DIGEST", "unavailable"))
    parser.add_argument(
        "--dashboard-image-digest",
        default=os.getenv("DASHBOARD_IMAGE_DIGEST", "unavailable"),
    )
    parser.add_argument("--sbom", action="append", default=[])
    args = parser.parse_args()

    commit_sha = command("git", "rev-parse", "HEAD")
    migration_heads = command("alembic", "heads").splitlines()
    dirty = bool(command("git", "status", "--porcelain"))
    generated_at = datetime.now(timezone.utc).isoformat()
    evidence = {
        "product": "LynxPay",
        "generated_at": generated_at,
        "commit_sha": commit_sha,
        "working_tree_dirty": dirty,
        "migration_heads": migration_heads,
        "images": {
            "api": args.api_image_digest,
            "dashboard": args.dashboard_image_digest,
        },
        "test_results": [file_evidence(path) for path in args.test_result],
        "sboms": [file_evidence(path) for path in args.sbom],
        "supported_payment_operations": [
            "M-PESA STK Push",
            "M-PESA STK status reconciliation",
            "M-PESA full reversal with maker-checker approval",
            "M-PESA callback ingestion and duplicate retention",
            "Merchant webhook delivery and replay",
            "Invoice payment links",
            "Walk-in M-PESA collection",
        ],
        "known_limitations": [
            "Kenya and KES only",
            "M-PESA Daraja only; Airtel Money has no supported public integration",
            "Full reversals only; partial reversal is not implemented",
            "No card, bank, accounting, settlement, or custody functionality",
            "Render first-time role provisioning requires an operator bootstrap step",
            "Production launch still requires approved Safaricom live credentials and external security review",
        ],
        "environment_variable_names": environment_names(),
        "rollback": {
            "command": "ops/render-deploy.sh",
            "required_inputs": [
                "RELEASE_SHA",
                "PREVIOUS_GOOD_SHA",
                "API_DEPLOY_HOOK_URL",
                "DASHBOARD_DEPLOY_HOOK_URL",
                "API_HEALTH_URL",
                "DASHBOARD_HEALTH_URL",
            ],
        },
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{commit_sha}.json"
    markdown_path = output_dir / f"{commit_sha}.md"
    json_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    tests = (
        "\n".join(
            f"- `{item['path']}`: "
            + (f"`sha256:{item['sha256']}`" if item["present"] else "missing")
            for item in evidence["test_results"]
        )
        or "- No test result artifacts supplied"
    )
    sboms = (
        "\n".join(
            f"- `{item['path']}`: "
            + (f"`sha256:{item['sha256']}`" if item["present"] else "missing")
            for item in evidence["sboms"]
        )
        or "- No SBOM artifacts supplied"
    )
    supported = "\n".join(f"- {item}" for item in evidence["supported_payment_operations"])
    limitations = "\n".join(f"- {item}" for item in evidence["known_limitations"])
    markdown_path.write_text(
        f"""# LynxPay release evidence

- Generated: `{generated_at}`
- Commit: `{commit_sha}`
- Dirty working tree: `{"yes" if dirty else "no"}`
- Migration head: `{", ".join(migration_heads)}`
- API image: `{args.api_image_digest}`
- Dashboard image: `{args.dashboard_image_digest}`

## Test evidence

{tests}

## SBOM evidence

{sboms}

## Supported payment operations

{supported}

## Known limitations

{limitations}

## Rollback

Run `ops/render-deploy.sh` with the failed release SHA, previous known-good SHA,
both private Render deploy hooks, and API/dashboard health URLs. The script
requires the target API health response to report the expected commit before it
accepts the deployment.
""",
        encoding="utf-8",
    )
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()

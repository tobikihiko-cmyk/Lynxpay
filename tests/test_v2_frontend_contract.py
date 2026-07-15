"""Static security and product contracts for the Version 2 merchant application."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "merchant-dashboard"


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_merchant_app_has_required_daraja_modules():
    required = (
        "src/app/(auth)/sign-up/page.tsx",
        "src/app/(auth)/sign-in/page.tsx",
        "src/app/(auth)/forgot-password/page.tsx",
        "src/app/(auth)/reset-password/page.tsx",
        "src/app/(auth)/verify-email/page.tsx",
        "src/app/(dashboard)/onboarding/page.tsx",
        "src/app/(dashboard)/payments/page.tsx",
        "src/app/(dashboard)/payments/[id]/page.tsx",
        "src/app/(dashboard)/reconciliation/page.tsx",
        "src/app/(dashboard)/api-keys/page.tsx",
        "src/app/(dashboard)/webhooks/page.tsx",
        "src/app/(dashboard)/audit/page.tsx",
        "src/app/(dashboard)/admin/merchants/page.tsx",
    )
    for path in required:
        assert (ROOT / path).is_file(), path
    combined = "\n".join(_read(path) for path in required)
    for term in (
        "M-PESA",
        "Daraja",
        "Reconciliation",
        "KES 1",
        "Production approval",
        "API keys",
        "Webhooks",
    ):
        assert term in combined


def test_bff_keeps_refresh_tokens_out_of_browser_storage_and_rotates_server_side():
    bff = _read("src/lib/bff.ts")
    proxy = _read("src/app/api/lynxpay/[...path]/route.ts")
    all_source = "\n".join(path.read_text() for path in ROOT.rglob("*.ts*"))
    assert 'httpOnly: true' in bff
    assert 'sameSite: "lax"' in bff
    assert 'secure' in bff
    assert 'cookies.set("lp_refresh"' in bff
    assert "/api/v1/auth/refresh" in proxy
    assert "Use the secure session endpoint" in proxy
    assert "localStorage" not in all_source
    assert "sessionStorage" not in all_source


def test_payment_ui_exposes_evidence_and_conservative_retry_contract():
    table = _read("src/components/payment-table.tsx")
    detail = _read("src/app/(dashboard)/payments/[id]/page.tsx")
    retry = _read("src/lib/payments.ts")
    assert "success_source" in table
    assert "receipt_status" in table
    assert "review_status" in table
    assert "provider_acceptance_state" in detail
    assert 'status === "failed"' in retry
    assert 'provider_acceptance_state === "rejected"' in retry
    assert "mpesa_receipt_number" in retry


def test_next_app_sets_browser_security_headers():
    config = _read("next.config.ts")
    for header in (
        "X-Content-Type-Options",
        "Referrer-Policy",
        "X-Frame-Options",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
    ):
        assert header in config

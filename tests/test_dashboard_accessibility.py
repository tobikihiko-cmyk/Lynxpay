"""Static dashboard accessibility and browser-security regression checks."""

from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


class DocumentAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.inline_scripts = 0
        self.inline_styles = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "script" and not attributes.get("src"):
            self.inline_scripts += 1
        if tag == "style":
            self.inline_styles += 1


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


def _luminance(value: str) -> float:
    channels = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in _rgb(value)
    ]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_dashboard_has_semantic_navigation_skip_link_and_no_inline_code():
    parser = DocumentAudit()
    parser.feed((ROOT / "dashboard/index.html").read_text())
    assert ("html", {"lang": "en"}) in parser.elements
    assert any(tag == "a" and attrs.get("href") == "#content" for tag, attrs in parser.elements)
    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.elements)
    assert any(tag == "nav" and attrs.get("aria-label") for tag, attrs in parser.elements)
    assert any(
        tag == "section" and attrs.get("aria-live") == "polite" for tag, attrs in parser.elements
    )
    assert parser.inline_scripts == 0
    assert parser.inline_styles == 0


def test_dynamic_forms_and_tables_include_accessibility_contracts():
    script = (ROOT / "dashboard/app.js").read_text()
    assert script.count("<label>") >= 12
    assert 'autocomplete="current-password"' in script
    assert 'autocomplete="new-password"' in script
    assert 'autocomplete="one-time-code"' in script
    assert script.count("<caption") >= 2
    assert "aria-busy" in script
    assert "renderRegistration" in script
    assert 'name="organization_name"' in script
    assert 'name="till_number"' in script
    assert "Production (live money)" in script
    assert "API base URL" not in script


def test_onboarding_wizard_covers_secure_six_step_merchant_lifecycle():
    script = (ROOT / "dashboard/app.js").read_text()
    for step in (
        "Create account",
        "Business profile",
        "M-PESA setup",
        "Daraja credentials",
        "Test payment",
        "Activation",
    ):
        assert step in script
    for field in (
        'name="business_type"',
        'name="county"',
        'name="town"',
        'name="support_email"',
        'name="shortcode_type"',
        'name="till_number"',
    ):
        assert field in script
    assert "/api/v1/organization" in script
    assert 'purpose: "merchant_verification"' in script
    assert "KES 1" in script
    assert "Callback confirmed" in script
    assert "/api/v1/api-keys" in script
    assert "shown again" in script
    assert 'value="${escapeHtml(merchant.callback_url)}" readonly' in script


def test_primary_dashboard_color_pairs_meet_wcag_aa_normal_text_contrast():
    assert _contrast("#0b1611", "#f2f4ef") >= 4.5
    assert _contrast("#617068", "#ffffff") >= 4.5
    assert _contrast("#9fb0a7", "#07120d") >= 4.5
    assert _contrast("#03150b", "#16c878") >= 4.5


def test_dashboard_visual_contract_is_daraja_specific_and_evidence_first():
    document = (ROOT / "dashboard/index.html").read_text()
    styles = (ROOT / "dashboard/styles.css").read_text()
    script = (ROOT / "dashboard/app.js").read_text()

    for label in ("M-PESA payments", "Daraja callbacks", "Safaricom Daraja"):
        assert label in document
    for selector in (".lynx-mark", ".metric-grid", ".status-success", ".auth-stage"):
        assert selector in styles
    assert "[hidden] { display: none !important; }" in styles
    for message in (
        "Payment certainty, engineered for",
        '<span class="no-break">M-PESA.</span>',
        "Captured volume",
        "Callback journal",
        "No aggregation layer",
        "Accepted is not paid",
    ):
        assert message in script
    assert "style=" not in script


def test_nginx_enforces_browser_security_headers():
    config = (ROOT / "dashboard/nginx.conf").read_text()
    for directive in (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
    ):
        assert re.search(rf"add_header\s+{directive}\b", config)
    assert "frame-ancestors 'none'" in config
    assert "object-src 'none'" in config
    assert "connect-src 'self'" in config
    assert "connect-src 'self' http" not in config
    assert "proxy_pass http://api:8000" in config

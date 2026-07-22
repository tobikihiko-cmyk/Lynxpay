"""Callback source trust and provider-burst rate budget tests."""

from starlette.requests import Request

from app.core.config import Settings, settings
from app.core.deps import get_client_ip
from app.observability import RedisRateLimitMiddleware


def _request(
    *,
    direct_ip: str,
    merchant_id: str = "merchant-one",
    headers: dict[str, str] | None = None,
) -> Request:
    encoded_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": f"/api/v1/callbacks/mpesa/{merchant_id}",
            "raw_path": f"/api/v1/callbacks/mpesa/{merchant_id}".encode(),
            "query_string": b"",
            "headers": encoded_headers,
            "client": (direct_ip, 41000),
            "server": ("api.lynxpay.test", 443),
        }
    )


def test_cloudflare_header_is_ignored_from_an_untrusted_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_CF_CONNECTING_IP", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request(direct_ip="203.0.113.42", headers={"cf-connecting-ip": "196.201.214.202"})
    assert get_client_ip(request) == "203.0.113.42"


def test_cloudflare_header_is_honored_only_from_a_trusted_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_CF_CONNECTING_IP", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request(direct_ip="10.20.30.40", headers={"cf-connecting-ip": "196.201.214.202"})
    assert get_client_ip(request) == "196.201.214.202"


def test_invalid_forwarded_address_is_not_used(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_CF_CONNECTING_IP", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request(direct_ip="10.20.30.40", headers={"x-forwarded-for": "not-an-ip"})
    assert get_client_ip(request) == "10.20.30.40"


def test_callback_budget_is_scoped_by_merchant_and_verified_source(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_CF_CONNECTING_IP", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "")
    monkeypatch.setattr(settings, "MPESA_CALLBACK_VERIFY_MODE", "ip_allowlist")
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "196.201.214.200/29")
    monkeypatch.setattr(settings, "RATE_LIMIT_CALLBACK_VERIFIED_REQUESTS_PER_MINUTE", 900)
    monkeypatch.setattr(settings, "RATE_LIMIT_CALLBACK_UNVERIFIED_REQUESTS_PER_MINUTE", 20)

    verified = RedisRateLimitMiddleware._callback_budget(
        _request(direct_ip="196.201.214.202", merchant_id="merchant-one")
    )
    other_merchant = RedisRateLimitMiddleware._callback_budget(
        _request(direct_ip="196.201.214.202", merchant_id="merchant-two")
    )
    unverified = RedisRateLimitMiddleware._callback_budget(
        _request(direct_ip="203.0.113.42", merchant_id="merchant-one")
    )

    assert verified[:2] == ("callback_verified", 900)
    assert other_merchant[:2] == ("callback_verified", 900)
    assert verified[2] != other_merchant[2]
    assert unverified[:2] == ("callback_unverified_or_malformed", 20)


def test_runtime_rejects_cloudflare_trust_without_proxy_cidrs():
    candidate = Settings(
        TRUST_CF_CONNECTING_IP=True,
        TRUSTED_PROXY_CIDRS="",
        DATABASE_URL="sqlite://",
    )
    try:
        candidate.validate_runtime()
    except RuntimeError as exc:
        assert "trusted proxy CIDRs" in str(exc)
    else:
        raise AssertionError("Cloudflare forwarding was enabled without a trusted peer network")

"""Opt-in Safaricom sandbox contract checks; never run or initiate STK by default."""

import os

import pytest

from app.daraja import DarajaClient, DarajaSecrets

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DARAJA_SANDBOX_TESTS") != "1",
    reason="set RUN_DARAJA_SANDBOX_TESTS=1 with dedicated sandbox credentials",
)


def _secrets() -> DarajaSecrets:
    required = [
        "DARAJA_SANDBOX_CONSUMER_KEY",
        "DARAJA_SANDBOX_CONSUMER_SECRET",
        "DARAJA_SANDBOX_PASSKEY",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"missing sandbox settings: {', '.join(missing)}")
    return DarajaSecrets(*(os.environ[name] for name in required))


@pytest.mark.asyncio
async def test_sandbox_oauth_contract():
    token = await DarajaClient("sandbox").get_access_token(_secrets())
    assert isinstance(token, str) and len(token) > 10


@pytest.mark.asyncio
async def test_sandbox_existing_checkout_status_contract():
    checkout_id = os.getenv("DARAJA_SANDBOX_CHECKOUT_REQUEST_ID")
    shortcode = os.getenv("DARAJA_SANDBOX_SHORTCODE")
    if not checkout_id or not shortcode:
        pytest.skip("status contract requires an existing sandbox checkout ID and shortcode")
    response, _ = await DarajaClient("sandbox").query_stk_status(
        secrets=_secrets(), shortcode=shortcode, checkout_request_id=checkout_id
    )
    assert isinstance(response, dict)
    assert "ResultCode" in response or "ResponseCode" in response or "errorCode" in response

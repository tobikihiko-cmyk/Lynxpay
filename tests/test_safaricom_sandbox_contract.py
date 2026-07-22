"""Opt-in Safaricom sandbox contract checks; never run or initiate STK by default."""

from decimal import Decimal
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


async def _stk_contract(*, shortcode_type: str) -> None:
    if os.getenv("RUN_DARAJA_SANDBOX_STK_TESTS") != "1":
        pytest.skip("set RUN_DARAJA_SANDBOX_STK_TESTS=1 to authorize a KES 1 STK request")
    phone = os.getenv("DARAJA_SANDBOX_TEST_PHONE")
    callback_url = os.getenv("DARAJA_SANDBOX_CALLBACK_URL")
    shortcode = os.getenv("DARAJA_SANDBOX_SHORTCODE")
    till_number = os.getenv("DARAJA_SANDBOX_TILL_NUMBER") if shortcode_type == "till" else None
    if not phone or not callback_url or not shortcode:
        pytest.skip("STK contract requires phone, callback URL, and shortcode")
    if shortcode_type == "till" and not till_number:
        pytest.skip("Till contract requires DARAJA_SANDBOX_TILL_NUMBER")
    assert callback_url.startswith("https://")
    response, payload = await DarajaClient("sandbox").stk_push(
        secrets=_secrets(),
        shortcode=shortcode,
        till_number=till_number,
        shortcode_type=shortcode_type,
        phone=phone,
        amount=Decimal("1"),
        external_reference=f"LYNXPAY-{shortcode_type.upper()}-CONTRACT",
        description="LynxPay controlled sandbox contract",
        callback_url=callback_url,
    )
    assert payload["TransactionType"] == (
        "CustomerPayBillOnline" if shortcode_type == "paybill" else "CustomerBuyGoodsOnline"
    )
    assert response.get("CheckoutRequestID")
    assert str(response.get("ResponseCode")) == "0"


@pytest.mark.asyncio
async def test_sandbox_paybill_stk_contract():
    await _stk_contract(shortcode_type="paybill")


@pytest.mark.asyncio
async def test_sandbox_till_stk_contract():
    await _stk_contract(shortcode_type="till")

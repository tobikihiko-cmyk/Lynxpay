"""Known and unknown M-PESA provider-result behavior."""

from app.provider_codes import classify_mpesa_result


def test_known_mpesa_result_codes_have_specific_categories():
    assert classify_mpesa_result("0").target == "success"
    assert classify_mpesa_result("1032").category == "customer_cancelled"
    assert classify_mpesa_result("1037").target == "timeout"
    assert classify_mpesa_result("1").category == "insufficient_funds"

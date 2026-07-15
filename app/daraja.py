"""Daraja transport adapter with no persistence assumptions."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx

SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"
REDACTION_MASK = "*" * 8


@dataclass(frozen=True)
class DarajaSecrets:
    consumer_key: str
    consumer_secret: str
    passkey: str


class DarajaRequestNotSentError(RuntimeError):
    """The STK request was definitely not submitted to Daraja."""


class DarajaSubmissionUncertainError(RuntimeError):
    """The STK request may have reached Daraja but acceptance is unverified."""


class DarajaClient:
    def __init__(self, environment: str):
        self.base_url = SANDBOX_BASE_URL if environment == "sandbox" else PRODUCTION_BASE_URL

    async def get_access_token(self, secrets: DarajaSecrets) -> str:
        basic = base64.b64encode(
            f"{secrets.consumer_key}:{secrets.consumer_secret}".encode()
        ).decode()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials",
                headers={"Authorization": f"Basic {basic}"},
                timeout=10,
            )
            response.raise_for_status()
            return response.json()["access_token"]

    async def stk_push(
        self,
        *,
        secrets: DarajaSecrets,
        shortcode: str,
        till_number: str | None,
        shortcode_type: str,
        phone: str,
        amount: Decimal,
        external_reference: str,
        description: str,
        callback_url: str,
    ) -> tuple[dict, dict]:
        try:
            token = await self.get_access_token(secrets)
        except Exception as exc:
            raise DarajaRequestNotSentError("Daraja OAuth failed before STK submission") from exc
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(f"{shortcode}{secrets.passkey}{timestamp}".encode()).decode()
        is_paybill = shortcode_type == "paybill"
        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,  # noqa: S105 - protocol field contains a generated request proof
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline" if is_paybill else "CustomerBuyGoodsOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": shortcode if is_paybill else (till_number or shortcode),
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": external_reference,
            "TransactionDesc": description,
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=15,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise DarajaRequestNotSentError("Daraja connection failed before submission") from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DarajaSubmissionUncertainError("Daraja submission outcome is uncertain") from exc
        if getattr(response, "status_code", 200) >= 500:
            raise DarajaSubmissionUncertainError("Daraja returned a server error after submission")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DarajaRequestNotSentError("Daraja rejected the STK HTTP request") from exc
        try:
            return response.json(), payload
        except ValueError as exc:
            raise DarajaSubmissionUncertainError(
                "Daraja returned an unreadable acceptance response"
            ) from exc

    async def query_stk_status(
        self,
        *,
        secrets: DarajaSecrets,
        shortcode: str,
        checkout_request_id: str,
    ) -> tuple[dict, dict]:
        """Verify an STK request through Daraja's status-query API."""

        token = await self.get_access_token(secrets)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(f"{shortcode}{secrets.passkey}{timestamp}".encode()).decode()
        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
            return response.json(), payload


def redact_stk_payload(payload: dict) -> dict:
    redacted = dict(payload)
    if "Password" in redacted:
        redacted["Password"] = REDACTION_MASK
    if phone := redacted.get("PhoneNumber"):
        redacted["PhoneNumber"] = f"{str(phone)[:6]}***{str(phone)[-3:]}"
    if party_a := redacted.get("PartyA"):
        redacted["PartyA"] = f"{str(party_a)[:6]}***{str(party_a)[-3:]}"
    return redacted

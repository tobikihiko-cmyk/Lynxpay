"""Daraja transport adapter with no persistence assumptions."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import time
import weakref

import httpx

from app.core.config import settings
from app.observability import DARAJA_TOKEN_CACHE

REDACTION_MASK = "*" * 8

_TOKEN_CACHE: dict[tuple[int, str, str], tuple[str, float]] = {}
_TOKEN_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}
_HTTP_CLIENTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def clear_daraja_token_cache() -> None:
    """Test/rotation hook; no credential material is retained in cache keys."""

    _TOKEN_CACHE.clear()
    _TOKEN_LOCKS.clear()


def _shared_http_client(base_url: str) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    clients = _HTTP_CLIENTS.setdefault(loop, {})
    client = clients.get(base_url)
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            trust_env=False,
        )
        clients[base_url] = client
    return client


async def close_daraja_clients() -> None:
    clients = [client for per_loop in list(_HTTP_CLIENTS.values()) for client in per_loop.values()]
    _HTTP_CLIENTS.clear()
    for client in clients:
        await client.aclose()


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
        self.base_url = (
            settings.DARAJA_SANDBOX_BASE_URL
            if environment == "sandbox"
            else settings.DARAJA_PRODUCTION_BASE_URL
        ).rstrip("/")

    async def get_access_token(self, secrets: DarajaSecrets) -> str:
        loop_id = id(asyncio.get_running_loop())
        credential_digest = hashlib.sha256(
            f"{secrets.consumer_key}\0{secrets.consumer_secret}".encode()
        ).hexdigest()
        cache_key = (loop_id, self.base_url, credential_digest)
        cached = _TOKEN_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and cached[1] > now:
            DARAJA_TOKEN_CACHE.labels("hit").inc()
            return cached[0]
        lock = _TOKEN_LOCKS.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = _TOKEN_CACHE.get(cache_key)
            now = time.monotonic()
            if cached and cached[1] > now:
                DARAJA_TOKEN_CACHE.labels("hit_after_wait").inc()
                return cached[0]
            DARAJA_TOKEN_CACHE.labels("miss").inc()
            token, expires_in = await self._fetch_access_token(secrets)
            # Safaricom normally returns 3599 seconds. Keep a safety window and
            # never cache an already-near-expiry token.
            ttl = max(min(expires_in, 3600) - 30, 1)
            _TOKEN_CACHE[cache_key] = (token, time.monotonic() + ttl)
            return token

    async def _fetch_access_token(self, secrets: DarajaSecrets) -> tuple[str, int]:
        basic = base64.b64encode(
            f"{secrets.consumer_key}:{secrets.consumer_secret}".encode()
        ).decode()
        response = await _shared_http_client(self.base_url).get(
            f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": f"Basic {basic}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            expires_in = int(payload.get("expires_in", 3599))
        except (TypeError, ValueError):
            expires_in = 3599
        return payload["access_token"], expires_in

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
        correlation_id: str | None = None,
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
            response = await _shared_http_client(self.base_url).post(
                f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    **({"X-LynxPay-Correlation-ID": correlation_id} if correlation_id else {}),
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
        correlation_id: str | None = None,
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
        response = await _shared_http_client(self.base_url).post(
            f"{self.base_url}/mpesa/stkpushquery/v1/query",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **({"X-LynxPay-Correlation-ID": correlation_id} if correlation_id else {}),
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json(), payload

    async def reverse_transaction(
        self,
        *,
        secrets: DarajaSecrets,
        initiator_name: str,
        security_credential: str,
        shortcode: str,
        transaction_id: str,
        amount: Decimal,
        remarks: str,
        result_url: str,
        timeout_url: str,
        occasion: str,
        correlation_id: str | None = None,
    ) -> tuple[dict, dict]:
        """Submit a full M-PESA transaction reversal to Daraja."""

        try:
            token = await self.get_access_token(secrets)
        except Exception as exc:
            raise DarajaRequestNotSentError(
                "Daraja OAuth failed before reversal submission"
            ) from exc
        payload = {
            "Initiator": initiator_name,
            "SecurityCredential": security_credential,
            "CommandID": "TransactionReversal",
            "TransactionID": transaction_id,
            "Amount": int(amount),
            "ReceiverParty": shortcode,
            "RecieverIdentifierType": "11",
            "ResultURL": result_url,
            "QueueTimeOutURL": timeout_url,
            "Remarks": remarks[:100],
            "Occasion": occasion[:100],
        }
        try:
            response = await _shared_http_client(self.base_url).post(
                f"{self.base_url}/mpesa/reversal/v1/request",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    **({"X-LynxPay-Correlation-ID": correlation_id} if correlation_id else {}),
                },
                timeout=15,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise DarajaRequestNotSentError(
                "Daraja connection failed before reversal submission"
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DarajaSubmissionUncertainError(
                "Daraja reversal submission outcome is uncertain"
            ) from exc
        if getattr(response, "status_code", 200) >= 500:
            raise DarajaSubmissionUncertainError(
                "Daraja returned a server error after reversal submission"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DarajaRequestNotSentError("Daraja rejected the reversal HTTP request") from exc
        try:
            return response.json(), payload
        except ValueError as exc:
            raise DarajaSubmissionUncertainError(
                "Daraja returned an unreadable reversal response"
            ) from exc


def redact_stk_payload(payload: dict) -> dict:
    redacted = dict(payload)
    if "Password" in redacted:
        redacted["Password"] = REDACTION_MASK
    if phone := redacted.get("PhoneNumber"):
        redacted["PhoneNumber"] = f"{str(phone)[:6]}***{str(phone)[-3:]}"
    if party_a := redacted.get("PartyA"):
        redacted["PartyA"] = f"{str(party_a)[:6]}***{str(party_a)[-3:]}"
    return redacted


def redact_reversal_payload(payload: dict) -> dict:
    redacted = dict(payload)
    if "SecurityCredential" in redacted:
        redacted["SecurityCredential"] = REDACTION_MASK
    if "Initiator" in redacted:
        redacted["Initiator"] = REDACTION_MASK
    return redacted

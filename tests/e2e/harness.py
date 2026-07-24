"""Private support services for the Docker-only browser test environment."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import secrets

from fastapi import FastAPI, HTTPException
import httpx
from sqlalchemy.orm import Session

from app.core.security import decrypt_sensitive_value
from app.database import SessionLocal
from app.models import AuthSession, EmailOutbox, User

daraja_app = FastAPI(title="LynxPay E2E Daraja Harness")
support_app = FastAPI(title="LynxPay E2E Support Harness")
background_tasks: set[asyncio.Task] = set()


async def _deliver_success_callback(callback_url: str, request_payload: dict, checkout_id: str):
    await asyncio.sleep(0.25)
    receipt = f"E2E{secrets.token_hex(5).upper()}"
    body = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": f"MR-{checkout_id}",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "E2E payment completed",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": request_payload["Amount"]},
                        {"Name": "MpesaReceiptNumber", "Value": receipt},
                        {
                            "Name": "TransactionDate",
                            "Value": int(datetime.now().strftime("%Y%m%d%H%M%S")),
                        },
                        {"Name": "PhoneNumber", "Value": request_payload["PhoneNumber"]},
                    ]
                },
            }
        }
    }
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(20):
            try:
                response = await client.post(callback_url, json=body)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)


@daraja_app.get("/oauth/v1/generate")
async def oauth_token():
    return {"access_token": "e2e-daraja-token", "expires_in": "3599"}


@daraja_app.post("/mpesa/stkpush/v1/processrequest")
async def stk_push(payload: dict):
    checkout_id = f"ws_CO_E2E_{secrets.token_hex(8)}"
    task = asyncio.create_task(
        _deliver_success_callback(payload["CallBackURL"], payload, checkout_id)
    )
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return {
        "ResponseCode": "0",
        "MerchantRequestID": f"MR-{checkout_id}",
        "CheckoutRequestID": checkout_id,
        "ResponseDescription": "Success. Request accepted for processing",
    }


@daraja_app.post("/mpesa/stkpushquery/v1/query")
async def stk_status(_payload: dict):
    return {
        "ResponseCode": "0",
        "ResultCode": "0",
        "ResultDesc": "The service request is processed successfully.",
    }


def _session() -> Session:
    return SessionLocal()


@support_app.get("/emails/latest")
def latest_email(to: str, template: str):
    with _session() as db:
        record = (
            db.query(EmailOutbox)
            .filter(EmailOutbox.to_email == to, EmailOutbox.template == template)
            .order_by(EmailOutbox.created_at.desc())
            .first()
        )
        if not record:
            raise HTTPException(status_code=404, detail="Email not queued")
        import json

        return json.loads(decrypt_sensitive_value(record.payload_encrypted))


@support_app.post("/sessions/expire-latest")
def expire_latest_session(email: str):
    with _session() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        session = (
            db.query(AuthSession)
            .filter(AuthSession.user_id == user.id, AuthSession.status == "active")
            .order_by(AuthSession.created_at.desc())
            .first()
        )
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.commit()
        return {"id": session.id, "status": "expired"}

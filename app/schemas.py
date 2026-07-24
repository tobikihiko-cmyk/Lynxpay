"""Validated public contracts for LynxPay Phase 1."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

KENYAN_MOBILE_RE = re.compile(r"^254(7\d{8}|1\d{8})$")


def normalize_kenyan_phone(value: str | int) -> str:
    phone = re.sub(r"[\s()-]", "", str(value).strip())
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    elif len(phone) == 9 and phone.startswith(("7", "1")):
        phone = "254" + phone
    if not KENYAN_MOBILE_RE.fullmatch(phone):
        raise ValueError("phone_number must be a valid Kenyan mobile number")
    return phone


class MerchantCreate(BaseModel):
    merchant_name: str = Field(min_length=2, max_length=200)
    shortcode: str = Field(pattern=r"^\d{5,12}$")
    till_number: str | None = Field(None, pattern=r"^\d{5,12}$")
    shortcode_type: Literal["paybill", "till", "store_number", "unknown"]
    environment: Literal["sandbox", "production"] = "sandbox"
    callback_url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_till_configuration(self):
        if self.shortcode_type in {"till", "store_number"} and not self.till_number:
            raise ValueError("till_number is required for Till and store-number merchants")
        return self


class OrganizationUpdate(BaseModel):
    legal_name: str | None = Field(None, min_length=2, max_length=250)
    business_type: str | None = Field(None, min_length=2, max_length=120)
    county: str | None = Field(None, min_length=2, max_length=100)
    town: str | None = Field(None, min_length=2, max_length=100)
    contact_phone: str | None = None
    support_email: EmailStr | None = None

    @field_validator("contact_phone")
    @classmethod
    def normalize_contact_phone(cls, value: str | None) -> str | None:
        return normalize_kenyan_phone(value) if value else None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one business-profile field is required")
        return self


class ConsentAcceptance(BaseModel):
    accept_terms: bool
    accept_privacy: bool
    terms_version: str = Field(min_length=1, max_length=50)
    privacy_version: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_explicit_acceptance(self):
        if not self.accept_terms or not self.accept_privacy:
            raise ValueError("terms and privacy must both be explicitly accepted")
        return self


class MerchantUpdate(BaseModel):
    merchant_name: str | None = Field(None, min_length=2, max_length=200)
    shortcode_type: Literal["paybill", "till", "store_number", "unknown"] | None = None
    status: (
        Literal[
            "active",
            "inactive",
            "suspended",
            "pending_setup",
            "credentials_added",
            "verified",
            "pending_approval",
            "rejected",
        ]
        | None
    ) = None
    callback_url: HttpUrl | None = None


class DarajaCredentialWrite(BaseModel):
    consumer_key: SecretStr = Field(min_length=1, max_length=500)
    consumer_secret: SecretStr = Field(min_length=1, max_length=500)
    passkey: SecretStr = Field(min_length=1, max_length=1000)
    shortcode: str = Field(pattern=r"^\d{5,12}$")
    initiator_name: SecretStr | None = None
    security_credential: SecretStr | None = None
    environment: Literal["sandbox", "production"]


class DarajaCredentialPatch(BaseModel):
    consumer_key: SecretStr | None = None
    consumer_secret: SecretStr | None = None
    passkey: SecretStr | None = None
    shortcode: str | None = Field(None, pattern=r"^\d{5,12}$")
    initiator_name: SecretStr | None = None
    security_credential: SecretStr | None = None

    @field_validator("security_credential")
    @classmethod
    def require_at_least_one_value(cls, value, info):
        return value


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    merchant_id: str | None = None
    environment: Literal["sandbox", "production"] = "sandbox"
    scopes: list[
        Literal[
            "merchants:read",
            "payments:read",
            "payments:write",
            "callbacks:read",
            "callbacks:read_raw",
            "webhooks:read",
            "webhooks:write",
            "audit:read",
        ]
    ] = Field(default_factory=lambda: ["payments:read", "payments:write", "callbacks:read"])
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def unique_nonempty_scopes(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one scope is required")
        return list(dict.fromkeys(value))


class StkPushCreate(BaseModel):
    merchant_id: str
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    phone_number: str
    external_reference: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=300)
    customer_name: str | None = Field(None, max_length=200)
    order_id: str | None = Field(None, max_length=120)
    invoice_id: str | None = Field(None, max_length=120)
    callback_metadata: dict[str, Any] | None = None
    purpose: Literal["payment", "merchant_verification"] = "payment"

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_kenyan_phone(value)

    @field_validator("amount")
    @classmethod
    def require_whole_shillings(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("Daraja STK Push amount must be a whole number of KES")
        return value.quantize(Decimal("0.01"))

    @model_validator(mode="after")
    def validate_merchant_verification_payment(self):
        if self.purpose == "merchant_verification" and self.amount != Decimal("1.00"):
            raise ValueError("merchant verification payments must be exactly KES 1")
        return self


class CatalogItemCreate(BaseModel):
    merchant_id: str
    item_type: Literal["service", "product"]
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(None, max_length=500)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=80)
    sort_order: int = Field(0, ge=0, le=999)

    @field_validator("unit_price")
    @classmethod
    def require_whole_shillings(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("M-PESA catalog prices must be whole KES")
        return value.quantize(Decimal("0.01"))


class CatalogItemPatch(BaseModel):
    item_type: Literal["service", "product"] | None = None
    name: str | None = Field(None, min_length=2, max_length=160)
    description: str | None = Field(None, max_length=500)
    unit_price: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=80)
    sort_order: int | None = Field(None, ge=0, le=999)
    status: Literal["active", "archived"] | None = None

    @field_validator("unit_price")
    @classmethod
    def require_whole_shillings(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value != value.to_integral_value():
            raise ValueError("M-PESA catalog prices must be whole KES")
        return value.quantize(Decimal("0.01"))

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one catalog field is required")
        return self


class InvoiceLineItemCreate(BaseModel):
    catalog_item_id: str | None = None
    item_type: Literal["service", "product", "custom"] | None = None
    name: str | None = Field(None, min_length=2, max_length=160)
    description: str | None = Field(None, max_length=500)
    quantity: Decimal = Field(Decimal("1.00"), gt=0, max_digits=10, decimal_places=2)
    unit_price: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)

    @field_validator("quantity")
    @classmethod
    def normalize_quantity(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @field_validator("unit_price")
    @classmethod
    def require_whole_shillings(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value != value.to_integral_value():
            raise ValueError("M-PESA invoice line prices must be whole KES")
        return value.quantize(Decimal("0.01"))

    @model_validator(mode="after")
    def require_catalog_or_custom_values(self):
        if self.catalog_item_id:
            return self
        if not self.item_type or not self.name or self.unit_price is None:
            raise ValueError("custom invoice lines require item_type, name, and unit_price")
        return self


class InvoiceCreate(BaseModel):
    merchant_id: str
    invoice_number: str | None = Field(None, min_length=1, max_length=80)
    client_name: str = Field(min_length=2, max_length=200)
    client_phone: str | None = None
    client_email: EmailStr | None = None
    service_title: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=2, max_length=2000)
    amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    line_items: list[InvoiceLineItemCreate] = Field(default_factory=list, max_length=20)
    due_at: datetime | None = None
    memo: str | None = Field(None, max_length=2000)

    @field_validator("client_phone")
    @classmethod
    def normalize_client_phone(cls, value: str | None) -> str | None:
        return normalize_kenyan_phone(value) if value else None

    @field_validator("amount")
    @classmethod
    def require_whole_shillings(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value != value.to_integral_value():
            raise ValueError("M-PESA invoice amount must be a whole number of KES")
        return value.quantize(Decimal("0.01"))

    @model_validator(mode="after")
    def require_amount_or_lines(self):
        if not self.line_items and self.amount is None:
            raise ValueError("invoice requires either amount or line_items")
        return self


class InvoicePayRequest(BaseModel):
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_kenyan_phone(value)


class PaymentRetryRequest(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
    allow_uncertain: bool = False


class ReversalRequestCreate(BaseModel):
    reason: str = Field(min_length=12, max_length=500)


class ReversalApproval(BaseModel):
    note: str | None = Field(None, max_length=500)


class WebhookEndpointCreate(BaseModel):
    merchant_id: str | None = None
    url: HttpUrl
    event_types: list[
        Literal[
            "payment.created",
            "payment.stk_sent",
            "payment.success",
            "payment.failed",
            "payment.timeout",
            "payment.unknown",
            "payment.reversed",
        ]
    ]

    @field_validator("event_types")
    @classmethod
    def require_events(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one event type is required")
        return list(dict.fromkeys(value))


class WebhookEndpointUpdate(BaseModel):
    url: HttpUrl | None = None
    event_types: (
        list[
            Literal[
                "payment.created",
                "payment.stk_sent",
                "payment.success",
                "payment.failed",
                "payment.timeout",
                "payment.unknown",
                "payment.reversed",
            ]
        ]
        | None
    ) = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.url is None and self.event_types is None and self.status is None:
            raise ValueError("at least one webhook endpoint change is required")
        if self.event_types is not None and not self.event_types:
            raise ValueError("event_types cannot be empty")
        return self

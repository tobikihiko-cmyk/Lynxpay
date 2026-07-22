"""Persistence models for the tenant-isolated LynxPay core."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import relationship

from app.database import Base

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)  # - deployed Python 3.10 compatibility


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class Organization(TimestampMixin, Base):
    __tablename__ = "lynxpay_organizations"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(200), nullable=False)
    legal_name = Column(String(250), nullable=True)
    business_type = Column(String(120), nullable=True)
    county = Column(String(100), nullable=True)
    town = Column(String(100), nullable=True)
    contact_email = Column(String(254), nullable=False)
    contact_phone = Column(String(20), nullable=True)
    support_email = Column(String(254), nullable=True)
    status = Column(String(30), nullable=False, default="active")
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    privacy_accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_terms_version = Column(String(50), nullable=True)
    accepted_privacy_version = Column(String(50), nullable=True)

    users = relationship("User", back_populates="organization")
    merchants = relationship("MerchantAccount", back_populates="organization")


class User(TimestampMixin, Base):
    """Native LynxPay organization member."""

    __tablename__ = "lynxpay_users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_lynxpay_user_org_email"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(254), nullable=False, unique=True, index=True)
    full_name = Column(String(200), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="owner")
    status = Column(String(30), nullable=False, default="active")
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    is_platform_admin = Column(Boolean, nullable=False, default=False)

    organization = relationship("Organization", back_populates="users")


class AuthSession(Base):
    __tablename__ = "lynxpay_auth_sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id = Column(String(36), nullable=False, index=True)
    refresh_token_prefix = Column(String(32), nullable=False, unique=True, index=True)
    refresh_token_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="active", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by_session_id = Column(
        String(36), ForeignKey("lynxpay_auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    mfa_authenticated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class PasswordResetToken(Base):
    __tablename__ = "lynxpay_password_reset_tokens"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="pending")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class EmailVerificationToken(Base):
    __tablename__ = "lynxpay_email_verification_tokens"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class MfaTotpCredential(TimestampMixin, Base):
    __tablename__ = "lynxpay_mfa_totp_credentials"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(36),
        ForeignKey("lynxpay_users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    secret_encrypted = Column(Text, nullable=False)
    encryption_key_version = Column(String(50), nullable=False)
    recovery_code_hashes = Column(JSON_TYPE, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_step = Column(Integer, nullable=True)


class EmailOutbox(Base):
    __tablename__ = "lynxpay_email_outbox"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    to_email = Column(String(254), nullable=False)
    template = Column(String(50), nullable=False)
    payload_encrypted = Column(Text, nullable=False)
    encryption_key_version = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    lease_owner = Column(String(100), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = "lynxpay_worker_heartbeats"

    worker_id = Column(String(100), primary_key=True)
    hostname = Column(String(255), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    processed_total = Column(Integer, nullable=False, default=0)
    metadata_json = Column("metadata", JSON_TYPE, nullable=True)


class TeamInvitation(Base):
    __tablename__ = "lynxpay_team_invitations"
    __table_args__ = (
        Index(
            "uq_lynxpay_pending_invite_org_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=Column("status", String) == "pending",
            sqlite_where=Column("status", String) == "pending",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(254), nullable=False, index=True)
    role = Column(String(30), nullable=False, default="operator")
    token_hash = Column(String(64), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="pending")
    invited_by_user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class MerchantAccount(TimestampMixin, Base):
    __tablename__ = "lynxpay_merchant_accounts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "shortcode", "environment", name="uq_lynxpay_org_shortcode_env"
        ),
        CheckConstraint(
            "shortcode_type IN ('paybill','till','store_number','unknown')",
            name="ck_lynxpay_merchant_shortcode_type",
        ),
        CheckConstraint(
            "environment IN ('sandbox','production')", name="ck_lynxpay_merchant_environment"
        ),
        CheckConstraint(
            "status IN ('active','inactive','suspended','pending_setup','credentials_added','verified','pending_approval','rejected')",
            name="ck_lynxpay_merchant_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_name = Column(String(200), nullable=False)
    shortcode = Column(String(20), nullable=False)
    till_number = Column(String(20), nullable=True)
    shortcode_type = Column(String(30), nullable=False, default="unknown")
    environment = Column(String(20), nullable=False, default="sandbox")
    status = Column(String(30), nullable=False, default="pending_setup")
    callback_url = Column(String(500), nullable=False)
    approval_submitted_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(String(500), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="merchants")
    credentials = relationship(
        "DarajaCredential", back_populates="merchant", cascade="all, delete-orphan"
    )
    payments = relationship("Payment", back_populates="merchant")


class DarajaCredential(TimestampMixin, Base):
    __tablename__ = "lynxpay_daraja_credentials"
    __table_args__ = (
        CheckConstraint(
            "environment IN ('sandbox','production')", name="ck_lynxpay_credential_environment"
        ),
        Index(
            "uq_lynxpay_active_credential",
            "merchant_account_id",
            unique=True,
            postgresql_where=Column("is_active", Boolean) == True,  # noqa: E712
            sqlite_where=Column("is_active", Boolean) == True,  # noqa: E712
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consumer_key_encrypted = Column(Text, nullable=False)
    consumer_secret_encrypted = Column(Text, nullable=False)
    passkey_encrypted = Column(Text, nullable=False)
    shortcode = Column(String(20), nullable=False)
    initiator_name_encrypted = Column(Text, nullable=True)
    security_credential_encrypted = Column(Text, nullable=True)
    environment = Column(String(20), nullable=False)
    encryption_key_version = Column(String(50), nullable=False, default="legacy")
    is_active = Column(Boolean, nullable=False, default=True)
    last_tested_at = Column(DateTime(timezone=True), nullable=True)

    merchant = relationship("MerchantAccount", back_populates="credentials")


class ApiKey(Base):
    __tablename__ = "lynxpay_api_keys"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    key_prefix = Column(String(32), nullable=False, unique=True, index=True)
    key_hash = Column(String(64), nullable=False)
    name = Column(String(120), nullable=False)
    environment = Column(String(20), nullable=False, default="sandbox", index=True)
    scopes = Column(JSON_TYPE, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="active")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Payment(TimestampMixin, Base):
    __tablename__ = "lynxpay_payments"
    __table_args__ = (
        UniqueConstraint(
            "merchant_account_id", "external_reference", name="uq_lynxpay_merchant_external_ref"
        ),
        UniqueConstraint(
            "merchant_account_id", "idempotency_key", name="uq_lynxpay_merchant_idempotency_key"
        ),
        UniqueConstraint("checkout_request_id", name="uq_lynxpay_checkout_request_id"),
        CheckConstraint("amount > 0", name="ck_lynxpay_payment_amount_positive"),
        CheckConstraint(
            "purpose IN ('payment','merchant_verification')",
            name="ck_lynxpay_payment_purpose",
        ),
        CheckConstraint(
            "status IN ('created','pending','stk_sent','success','failed','timeout','cancelled','reversed','unknown')",
            name="ck_lynxpay_payment_status",
        ),
        CheckConstraint(
            "success_source IN ('callback','status_query','manual_review','unknown')",
            name="ck_lynxpay_payment_success_source",
        ),
        CheckConstraint(
            "receipt_status IN ('present','missing','enriched_later','not_applicable')",
            name="ck_lynxpay_payment_receipt_status",
        ),
        CheckConstraint(
            "review_status IN ('none','needs_review','resolved')",
            name="ck_lynxpay_payment_review_status",
        ),
        CheckConstraint(
            "provider_acceptance_state IN ('not_sent','accepted','rejected','uncertain')",
            name="ck_lynxpay_payment_provider_acceptance",
        ),
        Index(
            "uq_lynxpay_merchant_receipt",
            "merchant_account_id",
            "mpesa_receipt_number",
            unique=True,
            postgresql_where=Column("mpesa_receipt_number", String).isnot(None),
            sqlite_where=Column("mpesa_receipt_number", String).isnot(None),
        ),
        Index("ix_lynxpay_payment_org_created", "organization_id", "created_at"),
        Index("ix_lynxpay_payment_merchant_created", "merchant_account_id", "created_at"),
        Index(
            "ix_lynxpay_payment_merchant_status_created",
            "merchant_account_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_lynxpay_payment_merchant_review_created",
            "merchant_account_id",
            "review_status",
            "created_at",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    external_reference = Column(String(120), nullable=False)
    idempotency_key = Column(String(255), nullable=True)
    idempotency_request_hash = Column(String(64), nullable=True)
    order_id = Column(String(120), nullable=True)
    invoice_id = Column(String(120), nullable=True)
    customer_name = Column(String(200), nullable=True)
    customer_phone = Column(String(15), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="KES")
    description = Column(String(300), nullable=False)
    purpose = Column(String(30), nullable=False, default="payment", index=True)
    callback_metadata = Column(JSON_TYPE, nullable=True)
    status = Column(String(30), nullable=False, default="created", index=True)
    success_source = Column(String(30), nullable=False, default="unknown")
    receipt_status = Column(String(30), nullable=False, default="missing")
    review_status = Column(String(30), nullable=False, default="none", index=True)
    review_reason = Column(String(500), nullable=True)
    provider_acceptance_state = Column(String(30), nullable=False, default="not_sent")
    checkout_request_id = Column(String(160), nullable=True, index=True)
    merchant_request_id = Column(String(160), nullable=True)
    mpesa_receipt_number = Column(String(100), nullable=True)
    result_code = Column(String(30), nullable=True)
    result_description = Column(String(500), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_attempts = Column(Integer, nullable=False, default=0)
    last_reconciled_at = Column(DateTime(timezone=True), nullable=True)
    next_reconciliation_at = Column(DateTime(timezone=True), nullable=True, index=True)
    reconciliation_lease_owner = Column(String(100), nullable=True, index=True)
    reconciliation_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    merchant = relationship("MerchantAccount", back_populates="payments")
    attempts = relationship(
        "PaymentAttempt", back_populates="payment", cascade="all, delete-orphan"
    )


class Invoice(TimestampMixin, Base):
    __tablename__ = "lynxpay_invoices"
    __table_args__ = (
        UniqueConstraint(
            "merchant_account_id", "invoice_number", name="uq_lynxpay_merchant_invoice_number"
        ),
        UniqueConstraint("public_id", name="uq_lynxpay_invoice_public_id"),
        CheckConstraint("amount > 0", name="ck_lynxpay_invoice_amount_positive"),
        CheckConstraint("currency = 'KES'", name="ck_lynxpay_invoice_currency_kes"),
        CheckConstraint(
            "status IN ('draft','sent','paid','void','expired')",
            name="ck_lynxpay_invoice_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_number = Column(String(80), nullable=False)
    public_id = Column(String(80), nullable=False, index=True)
    client_name = Column(String(200), nullable=False)
    client_phone = Column(String(15), nullable=True)
    client_email = Column(String(254), nullable=True)
    service_title = Column(String(160), nullable=False)
    description = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="KES")
    status = Column(String(20), nullable=False, default="sent", index=True)
    due_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    payment_id = Column(
        String(36), ForeignKey("lynxpay_payments.id", ondelete="SET NULL"), nullable=True
    )
    merchant_display_name = Column(String(200), nullable=False)
    merchant_display_address = Column(String(300), nullable=True)
    merchant_display_email = Column(String(254), nullable=True)
    merchant_display_phone = Column(String(20), nullable=True)
    memo = Column(Text, nullable=True)

    merchant = relationship("MerchantAccount")
    payment = relationship("Payment", foreign_keys=[payment_id])
    line_items = relationship(
        "InvoiceLineItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.position",
    )


class CatalogItem(TimestampMixin, Base):
    __tablename__ = "lynxpay_catalog_items"
    __table_args__ = (
        UniqueConstraint(
            "merchant_account_id", "name", name="uq_lynxpay_merchant_catalog_item_name"
        ),
        CheckConstraint("item_type IN ('service','product')", name="ck_lynxpay_catalog_item_type"),
        CheckConstraint("unit_price > 0", name="ck_lynxpay_catalog_item_price_positive"),
        CheckConstraint("currency = 'KES'", name="ck_lynxpay_catalog_item_currency_kes"),
        CheckConstraint("status IN ('active','archived')", name="ck_lynxpay_catalog_item_status"),
        Index(
            "ix_lynxpay_catalog_merchant_status_sort",
            "merchant_account_id",
            "status",
            "sort_order",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_type = Column(String(20), nullable=False)
    name = Column(String(160), nullable=False)
    description = Column(String(500), nullable=True)
    unit_price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="KES")
    sku = Column(String(80), nullable=True)
    status = Column(String(20), nullable=False, default="active", index=True)
    sort_order = Column(Integer, nullable=False, default=0)

    merchant = relationship("MerchantAccount")


class InvoiceLineItem(Base):
    __tablename__ = "lynxpay_invoice_line_items"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ('service','product','custom')",
            name="ck_lynxpay_invoice_line_item_type",
        ),
        CheckConstraint("quantity > 0", name="ck_lynxpay_invoice_line_quantity_positive"),
        CheckConstraint("unit_price > 0", name="ck_lynxpay_invoice_line_price_positive"),
        CheckConstraint("line_total > 0", name="ck_lynxpay_invoice_line_total_positive"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    invoice_id = Column(
        String(36),
        ForeignKey("lynxpay_invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    catalog_item_id = Column(
        String(36), ForeignKey("lynxpay_catalog_items.id", ondelete="SET NULL"), nullable=True
    )
    position = Column(Integer, nullable=False, default=0)
    item_type = Column(String(20), nullable=False)
    name = Column(String(160), nullable=False)
    description = Column(String(500), nullable=True)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)

    invoice = relationship("Invoice", back_populates="line_items")
    catalog_item = relationship("CatalogItem")


class PaymentAttempt(TimestampMixin, Base):
    __tablename__ = "lynxpay_payment_attempts"
    __table_args__ = (
        UniqueConstraint("payment_id", "attempt_number", name="uq_lynxpay_payment_attempt_number"),
        UniqueConstraint("checkout_request_id", name="uq_lynxpay_attempt_checkout_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    payment_id = Column(
        String(36),
        ForeignKey("lynxpay_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number = Column(Integer, nullable=False)
    phone_number = Column(String(15), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    request_payload_redacted = Column(JSON_TYPE, nullable=False)
    response_payload = Column(JSON_TYPE, nullable=True)
    merchant_request_id = Column(String(160), nullable=True)
    checkout_request_id = Column(String(160), nullable=True)
    response_code = Column(String(30), nullable=True)
    response_description = Column(String(500), nullable=True)
    # Submission evidence is intentionally more precise than the payment state.
    # A worker can recover `submitting` attempts after a process/network failure
    # without guessing whether the request was ever attempted.
    status = Column(String(30), nullable=False, default="not_sent", index=True)
    submission_started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    provider_responded_at = Column(DateTime(timezone=True), nullable=True)
    abandoned_at = Column(DateTime(timezone=True), nullable=True)
    attempt_type = Column(String(20), nullable=False, default="initial")
    retry_reason = Column(String(500), nullable=True)
    initiated_by_user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True
    )
    initiated_by_api_key_id = Column(
        String(36), ForeignKey("lynxpay_api_keys.id", ondelete="SET NULL"), nullable=True
    )

    payment = relationship("Payment", back_populates="attempts")


class MpesaCallback(Base):
    __tablename__ = "lynxpay_mpesa_callbacks"
    __table_args__ = (
        Index("ix_lynxpay_callback_merchant_received", "merchant_account_id", "received_at"),
        Index("ix_lynxpay_callback_checkout_result", "checkout_request_id", "result_code"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_id = Column(
        String(36),
        ForeignKey("lynxpay_payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    checkout_request_id = Column(String(160), nullable=True)
    merchant_request_id = Column(String(160), nullable=True)
    mpesa_receipt_number = Column(String(100), nullable=True)
    callback_amount = Column(Numeric(12, 2), nullable=True)
    callback_phone = Column(String(15), nullable=True)
    result_code = Column(String(30), nullable=True)
    result_description = Column(String(500), nullable=True)
    raw_payload = Column(JSON_TYPE, nullable=False)
    raw_body = Column(Text, nullable=False)
    processed = Column(Boolean, nullable=False, default=False)
    processing_status = Column(String(40), nullable=False, default="received", index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    duplicate_of_callback_id = Column(
        String(36), ForeignKey("lynxpay_mpesa_callbacks.id", ondelete="SET NULL"), nullable=True
    )
    linked_at = Column(DateTime(timezone=True), nullable=True)
    linked_by_user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True
    )
    link_reason = Column(String(500), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    source_ip = Column(String(45), nullable=True)


class PaymentStatusCheck(Base):
    __tablename__ = "lynxpay_payment_status_checks"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_id = Column(
        String(36),
        ForeignKey("lynxpay_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkout_request_id = Column(String(160), nullable=False)
    result_code = Column(String(30), nullable=True)
    result_description = Column(String(500), nullable=True)
    raw_response = Column(JSON_TYPE, nullable=False)
    outcome = Column(String(30), nullable=False)
    checked_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class WebhookEndpoint(TimestampMixin, Base):
    __tablename__ = "lynxpay_webhook_endpoints"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    url = Column(String(1000), nullable=False)
    event_types = Column(JSON_TYPE, nullable=False, default=list)
    secret_encrypted = Column(Text, nullable=False)
    encryption_key_version = Column(String(50), nullable=False, default="legacy")
    status = Column(String(20), nullable=False, default="active")
    consecutive_failures = Column(Integer, nullable=False, default=0)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    pause_reason = Column(String(500), nullable=True)


class WebhookDelivery(TimestampMixin, Base):
    __tablename__ = "lynxpay_webhook_deliveries"

    id = Column(String(36), primary_key=True, default=_uuid)
    webhook_endpoint_id = Column(
        String(36),
        ForeignKey("lynxpay_webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_id = Column(
        String(36),
        ForeignKey("lynxpay_payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    replay_of_delivery_id = Column(
        String(36), ForeignKey("lynxpay_webhook_deliveries.id", ondelete="SET NULL"), nullable=True
    )
    event_type = Column(String(80), nullable=False, index=True)
    payload = Column(JSON_TYPE, nullable=False)
    status = Column(String(30), nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=8)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    lease_owner = Column(String(100), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)


class WebhookDeliveryAttempt(Base):
    __tablename__ = "lynxpay_webhook_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "webhook_delivery_id",
            "attempt_number",
            name="uq_lynxpay_webhook_delivery_attempt_number",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    webhook_delivery_id = Column(
        String(36),
        ForeignKey("lynxpay_webhook_deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="started")
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class PaymentLedgerEntry(Base):
    """Append-only state ledger; no merchant funds or balances are represented."""

    __tablename__ = "lynxpay_payment_ledger"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_id = Column(
        String(36),
        ForeignKey("lynxpay_payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(80), nullable=False)
    status_from = Column(String(30), nullable=True)
    status_to = Column(String(30), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="KES")
    details = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "lynxpay_audit_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    organization_id = Column(
        String(36),
        ForeignKey("lynxpay_organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    merchant_account_id = Column(
        String(36),
        ForeignKey("lynxpay_merchant_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_user_id = Column(
        String(36), ForeignKey("lynxpay_users.id", ondelete="SET NULL"), nullable=True
    )
    actor_api_key_id = Column(
        String(36), ForeignKey("lynxpay_api_keys.id", ondelete="SET NULL"), nullable=True
    )
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(80), nullable=False)
    entity_id = Column(String(100), nullable=False)
    metadata_json = Column("metadata", JSON_TYPE, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


def _immutable(_mapper, _connection, target) -> None:
    raise ValueError(f"{target.__class__.__name__} records are append-only")


for _model in (AuditLog, PaymentLedgerEntry):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)


@event.listens_for(OrmSession, "before_flush")
def _require_ledger_for_payment_status_change(session, _flush_context, _instances) -> None:
    """Reject ORM payment transitions that do not carry ledger evidence."""

    new_ledger_rows = [row for row in session.new if isinstance(row, PaymentLedgerEntry)]
    for payment in session.dirty:
        if not isinstance(payment, Payment):
            continue
        history = inspect(payment).attrs.status.history
        if not history.has_changes() or not history.deleted:
            continue
        previous = history.deleted[0]
        has_evidence = any(
            row.payment_id == payment.id
            and row.status_from == previous
            and row.status_to == payment.status
            for row in new_ledger_rows
        )
        if not has_evidence:
            raise ValueError("Payment status changes require an append-only ledger entry")

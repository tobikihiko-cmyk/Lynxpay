"""Opt-in PostgreSQL callback concurrency and real-role RLS tests."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.models import (
    DarajaCredential,
    MerchantAccount,
    MpesaCallback,
    Organization,
    Payment,
    PaymentLedgerEntry,
)
from app.service import ledger, utcnow
from app.state_machine import transition_payment

URL = os.getenv("POSTGRES_TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not URL, reason="POSTGRES_TEST_DATABASE_URL is not configured")


def _drop_test_role(connection, role: str) -> None:
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
    ).scalar()
    if exists:
        connection.execute(text(f"DROP OWNED BY {role}"))
        connection.execute(text(f"DROP ROLE {role}"))


@pytest.fixture(scope="module")
def postgres_engine():
    assert "test" in URL.lower(), "PostgreSQL tests require a dedicated disposable test database"
    migration_env = {**os.environ, "DATABASE_URL": URL}
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=ROOT,
        env=migration_env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=migration_env,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=migration_env,
            check=True,
            capture_output=True,
            text=True,
        )


def test_simultaneous_success_callbacks_transition_once(postgres_engine):
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    with session_factory.begin() as db:
        organization = Organization(
            name="Concurrency", contact_email="c@example.test", status="active"
        )
        db.add(organization)
        db.flush()
        merchant = MerchantAccount(
            organization_id=organization.id,
            merchant_name="Concurrent merchant",
            shortcode="123456",
            shortcode_type="paybill",
            environment="sandbox",
            status="active",
            callback_url="https://pay.example.test/callback",
        )
        db.add(merchant)
        db.flush()
        payment = Payment(
            organization_id=organization.id,
            merchant_account_id=merchant.id,
            external_reference="CONCURRENT-1",
            customer_phone="254712345678",
            amount=Decimal("100"),
            currency="KES",
            description="Concurrency",
            status="stk_sent",
            checkout_request_id="ws_CONCURRENT_1",
        )
        db.add(payment)
    payment_id, merchant_id = payment.id, merchant.id

    def process_callback() -> None:
        with session_factory.begin() as db:
            locked = db.query(Payment).filter_by(id=payment_id).with_for_update().one()
            duplicate = locked.status == "success"
            callback = MpesaCallback(
                merchant_account_id=merchant_id,
                payment_id=payment_id,
                checkout_request_id=locked.checkout_request_id,
                result_code="0",
                mpesa_receipt_number="RCP-CONCURRENT",
                raw_payload={"result": 0},
                raw_body='{"result":0}',
                processed=not duplicate,
                processed_at=utcnow() if not duplicate else None,
                received_at=utcnow(),
            )
            db.add(callback)
            if not duplicate:
                previous = transition_payment(locked, "success")
                locked.mpesa_receipt_number = "RCP-CONCURRENT"
                ledger(db, payment=locked, event_type="payment.success", status_from=previous)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: process_callback(), range(2)))

    with session_factory() as db:
        assert db.query(Payment).filter_by(id=payment_id, status="success").count() == 1
        assert db.query(MpesaCallback).filter_by(payment_id=payment_id).count() == 2
        assert (
            db.query(PaymentLedgerEntry)
            .filter_by(payment_id=payment_id, event_type="payment.success")
            .count()
            == 1
        )


def test_rls_hides_and_blocks_cross_tenant_rows_for_non_owner_role(postgres_engine):
    owner_sessions = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    with owner_sessions.begin() as db:
        tenants = []
        for number in (1, 2):
            organization = Organization(
                name=f"RLS tenant {number}",
                contact_email=f"rls-{number}@example.test",
                status="active",
            )
            db.add(organization)
            db.flush()
            merchant = MerchantAccount(
                organization_id=organization.id,
                merchant_name=f"RLS merchant {number}",
                shortcode=f"98765{number}",
                shortcode_type="paybill",
                environment="sandbox",
                status="active",
                callback_url=f"https://pay.example.test/callback/{number}",
            )
            db.add(merchant)
            db.flush()
            credential = DarajaCredential(
                merchant_account_id=merchant.id,
                consumer_key_encrypted="env1::test::wrapped::ciphertext",
                consumer_secret_encrypted="env1::test::wrapped::ciphertext",
                passkey_encrypted="env1::test::wrapped::ciphertext",
                shortcode=merchant.shortcode,
                environment="sandbox",
                encryption_key_version="test",
                is_active=True,
            )
            payment = Payment(
                organization_id=organization.id,
                merchant_account_id=merchant.id,
                external_reference=f"RLS-{number}",
                customer_phone="254712345678",
                amount=Decimal("100"),
                currency="KES",
                description="RLS verification",
                status="stk_sent",
                checkout_request_id=f"ws_RLS_{number}",
            )
            db.add_all([credential, payment])
            db.flush()
            ledger(db, payment=payment, event_type="payment.stk_sent", status_from="pending")
            tenants.append((organization.id, payment.id))

    role = "lynxpay_rls_test"
    worker_role = "lynxpay_worker_test"
    password = "rls-test-password-only"
    with postgres_engine.begin() as connection:
        _drop_test_role(connection, role)
        _drop_test_role(connection, worker_role)
        connection.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD 'rls-test-password-only' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        )
        connection.execute(
            text(
                f"CREATE ROLE {worker_role} LOGIN PASSWORD 'worker-test-password-only' "
                "NOSUPERUSER BYPASSRLS"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {worker_role}"))
        connection.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {worker_role}"
            )
        )

    restricted_url = make_url(URL).set(username=role, password=password)
    restricted_engine = create_engine(restricted_url, pool_pre_ping=True)
    restricted_sessions = sessionmaker(bind=restricted_engine)
    worker_url = make_url(URL).set(username=worker_role, password="worker-test-password-only")
    worker_engine = create_engine(worker_url, pool_pre_ping=True)
    try:
        with restricted_sessions.begin() as db:
            db.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": tenants[0][0]},
            )
            visible = db.query(Payment).order_by(Payment.external_reference).all()
            assert [payment.id for payment in visible] == [tenants[0][1]]
            assert db.query(DarajaCredential).count() == 1
            changed = db.execute(
                text("UPDATE lynxpay_payments SET description = 'blocked' WHERE id = :id"),
                {"id": tenants[1][1]},
            )
            assert changed.rowcount == 0

        with restricted_engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": tenants[0][0]},
            )
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "UPDATE lynxpay_payment_ledger SET event_type = 'tampered' "
                        "WHERE payment_id = :payment_id"
                    ),
                    {"payment_id": tenants[0][1]},
                )
            transaction.rollback()

        with restricted_sessions.begin() as db:
            assert db.query(Payment).count() == 0
        with sessionmaker(bind=worker_engine).begin() as db:
            assert (
                db.query(Payment).filter(Payment.id.in_([tenants[0][1], tenants[1][1]])).count()
                == 2
            )
    finally:
        restricted_engine.dispose()
        worker_engine.dispose()
        with postgres_engine.begin() as connection:
            _drop_test_role(connection, role)
            _drop_test_role(connection, worker_role)

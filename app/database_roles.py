"""Production database-identity invariants."""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import settings


def validate_runtime_database_role(engine, purpose: str) -> None:
    """Refuse privileged PostgreSQL identities in runtime processes."""

    if not settings.is_production or engine.dialect.name != "postgresql":
        return
    with engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT current_user, r.rolsuper, r.rolbypassrls "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
        ).one()
        if role.rolsuper or role.rolbypassrls:
            raise RuntimeError(f"The {purpose} database role must be NOSUPERUSER and NOBYPASSRLS")
        owns_payment_tables = connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.oid = to_regclass('public.lynxpay_payments') "
                "AND r.rolname = current_user)"
            )
        ).scalar()
        if owns_payment_tables:
            raise RuntimeError(f"The {purpose} database role must not own payment-plane tables")

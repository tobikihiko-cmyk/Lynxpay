"""Runtime database identity fail-closed tests."""

from types import SimpleNamespace

import pytest

from app.core.config import Settings, settings
from app.database_roles import validate_runtime_database_role


class _Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def scalar(self):
        return self.value


class _Connection:
    def __init__(self, role, owns_tables):
        self.role = role
        self.owns_tables = owns_tables

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement):
        if "pg_roles" in str(statement) and "rolsuper" in str(statement):
            return _Result(self.role)
        return _Result(self.owns_tables)


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, role, owns_tables=False):
        self.connection = _Connection(role, owns_tables)

    def connect(self):
        return self.connection


def test_runtime_rejects_superuser_and_bypassrl(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="NOSUPERUSER and NOBYPASSRLS"):
        validate_runtime_database_role(
            _Engine(SimpleNamespace(rolsuper=False, rolbypassrls=True)), "worker"
        )
    with pytest.raises(RuntimeError, match="NOSUPERUSER and NOBYPASSRLS"):
        validate_runtime_database_role(
            _Engine(SimpleNamespace(rolsuper=True, rolbypassrls=False)), "API"
        )


def test_runtime_rejects_payment_table_owner(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    engine = _Engine(SimpleNamespace(rolsuper=False, rolbypassrls=False), owns_tables=True)
    with pytest.raises(RuntimeError, match="must not own payment-plane tables"):
        validate_runtime_database_role(engine, "API")


def test_non_privileged_non_owner_runtime_role_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    engine = _Engine(SimpleNamespace(rolsuper=False, rolbypassrls=False), owns_tables=False)
    validate_runtime_database_role(engine, "worker")


def _production_settings(**overrides):
    values = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "x" * 32,
        "PUBLIC_BASE_URL": "https://pay.example.test",
        "ENCRYPTION_KEYS_JSON": '{"v1":"key"}',
        "MPESA_CALLBACK_IP_ALLOWLIST": "196.201.214.200/29",
        "EMAIL_DELIVERY_MODE": "smtp",
        "SMTP_HOST": "smtp.example.test",
        "RATE_LIMIT_ENABLED": True,
        "REDIS_URL": "redis://redis:6379/0",
        "REQUIRE_PRIVILEGED_MFA": True,
        "METRICS_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_api_does_not_require_or_receive_worker_database_url():
    candidate = _production_settings(
        PROCESS_TYPE="api",
        DATABASE_URL="postgresql://api/db",
        ADMIN_DATABASE_URL="postgresql://admin/db",
        WORKER_DATABASE_URL="",
    )
    candidate.validate_runtime()


def test_production_worker_does_not_require_admin_or_metrics_database_urls():
    candidate = _production_settings(
        PROCESS_TYPE="worker",
        DATABASE_URL="postgresql://worker/db",
        WORKER_DATABASE_URL="postgresql://worker/db",
        ADMIN_DATABASE_URL="",
        METRICS_DATABASE_URL="",
    )
    candidate.validate_runtime()


def test_production_worker_requires_dedicated_worker_database_url():
    candidate = _production_settings(PROCESS_TYPE="worker", WORKER_DATABASE_URL="")
    with pytest.raises(RuntimeError, match="dedicated worker database role"):
        candidate.validate_runtime()

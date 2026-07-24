"""Environment-only LynxPay configuration."""

import ipaddress
import json

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "LynxPay"
    APP_VERSION: str = "0.1.0"
    RELEASE_SHA: str = ""
    RENDER_GIT_COMMIT: str = ""
    ENVIRONMENT: str = "development"
    PROCESS_TYPE: str = "api"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql://lynxpay:lynxpay@localhost:5432/lynxpay"
    WORKER_DATABASE_URL: str = ""
    ADMIN_DATABASE_URL: str = ""
    SECRET_KEY: str = "change-me-development-secret-at-least-32-characters"
    SECRET_ENCRYPTION_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24
    MFA_ISSUER: str = "LynxPay"
    REQUIRE_PRIVILEGED_MFA: bool = False
    MFA_PRIVILEGED_MAX_AGE_MINUTES: int = 30
    TERMS_VERSION: str = "2026-07-16"
    PRIVACY_VERSION: str = "2026-07-16"

    DASHBOARD_PUBLIC_URL: str = "http://localhost:3000"
    EMAIL_DELIVERY_MODE: str = "outbox"
    EMAIL_FROM: str = "no-reply@lynxpay.local"
    EMAIL_MAX_ATTEMPTS: int = 5
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True

    REDIS_URL: str = ""
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 120
    RATE_LIMIT_AUTH_REQUESTS_PER_MINUTE: int = 10
    RATE_LIMIT_FAIL_CLOSED: bool = True

    METRICS_ENABLED: bool = True
    METRICS_BEARER_TOKEN: str = ""
    METRICS_DATABASE_URL: str = ""
    OTEL_SERVICE_NAME: str = "lynxpay"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    PUBLIC_BASE_URL: str = ""
    RENDER_EXTERNAL_URL: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    TRUSTED_PROXY_CIDRS: str = "127.0.0.1/32"
    TRUST_CF_CONNECTING_IP: bool = False
    MPESA_CALLBACK_VERIFY_MODE: str = "ip_allowlist"
    MPESA_CALLBACK_IP_ALLOWLIST: str = "196.201.214.200/29,196.201.214.208/29"
    MPESA_WEBHOOK_SECRET: str = ""
    DARAJA_SANDBOX_BASE_URL: str = "https://sandbox.safaricom.co.ke"
    DARAJA_PRODUCTION_BASE_URL: str = "https://api.safaricom.co.ke"
    MAX_CALLBACK_BODY_BYTES: int = 65536
    RATE_LIMIT_CALLBACK_REQUESTS_PER_MINUTE: int = 60
    RATE_LIMIT_CALLBACK_VERIFIED_REQUESTS_PER_MINUTE: int = 600
    RATE_LIMIT_CALLBACK_UNVERIFIED_REQUESTS_PER_MINUTE: int = 30

    WEBHOOK_MAX_ATTEMPTS: int = 8
    WEBHOOK_RETRY_BASE_SECONDS: int = 30
    WEBHOOK_LEASE_SECONDS: int = 60
    WEBHOOK_CONNECT_TIMEOUT_SECONDS: float = 5.0
    WEBHOOK_TOTAL_TIMEOUT_SECONDS: float = 10.0
    WEBHOOK_MAX_RESPONSE_BYTES: int = 16384
    WEBHOOK_CLAIM_PER_ENDPOINT: int = 5
    WEBHOOK_AUTO_PAUSE_FAILURES: int = 20

    RECONCILIATION_INITIAL_DELAY_SECONDS: int = 120
    RECONCILIATION_INTERVAL_SECONDS: int = 300
    RECONCILIATION_FREQUENT_INTERVAL_SECONDS: int = 60
    RECONCILIATION_SLOW_INTERVAL_SECONDS: int = 300
    RECONCILIATION_OCCASIONAL_INTERVAL_SECONDS: int = 3600
    RECONCILIATION_MANUAL_REVIEW_AFTER_HOURS: int = 24
    RECONCILIATION_MAX_ATTEMPTS: int = 40
    STK_SUBMITTING_TIMEOUT_SECONDS: int = 120
    REVERSAL_LEASE_SECONDS: int = 60
    MAINTENANCE_BATCH_SIZE: int = 100
    WORKER_HEARTBEAT_MAX_AGE_SECONDS: int = 120
    RETENTION_DELETION_ENABLED: bool = False
    CALLBACK_RETENTION_DAYS: int = 400
    WEBHOOK_DELIVERY_RETENTION_DAYS: int = 180
    STATUS_CHECK_RETENTION_DAYS: int = 400
    AUDIT_RETENTION_DAYS: int = 2555
    LEDGER_RETENTION_DAYS: int = 2555

    ENCRYPTION_ACTIVE_KEY_ID: str = "v1"
    ENCRYPTION_PROVIDER: str = "local"
    ENCRYPTION_KEYS_JSON: str = ""
    ENCRYPTION_KMS_KEY_IDS_JSON: str = ""
    AWS_REGION: str = ""

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "released", "prod", "production"}:
                return False
            if normalized in {"debug", "dev", "development"}:
                return True
        return value

    @field_validator("MPESA_CALLBACK_VERIFY_MODE")
    @classmethod
    def validate_callback_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"ip_allowlist", "hmac"}:
            raise ValueError("MPESA_CALLBACK_VERIFY_MODE must be ip_allowlist or hmac")
        return normalized

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"production", "prod"}

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.ALLOWED_ORIGINS.split(",") if item.strip()]

    @property
    def mpesa_callback_hmac(self) -> bool:
        return self.MPESA_CALLBACK_VERIFY_MODE == "hmac"

    @property
    def public_url(self) -> str:
        return (self.PUBLIC_BASE_URL or self.RENDER_EXTERNAL_URL).rstrip("/")

    @property
    def release_sha(self) -> str:
        return self.RELEASE_SHA or self.RENDER_GIT_COMMIT or "development"

    def validate_runtime(self) -> None:
        process_type = self.PROCESS_TYPE.strip().lower()
        if process_type not in {"api", "worker", "migration", "cli"}:
            raise RuntimeError("PROCESS_TYPE must be api, worker, migration, or cli")
        proxy_cidrs = [item.strip() for item in self.TRUSTED_PROXY_CIDRS.split(",") if item.strip()]
        try:
            for cidr in proxy_cidrs:
                ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise RuntimeError("TRUSTED_PROXY_CIDRS contains an invalid network") from exc
        if self.TRUST_CF_CONNECTING_IP and not proxy_cidrs:
            raise RuntimeError(
                "TRUST_CF_CONNECTING_IP requires explicitly configured trusted proxy CIDRs"
            )
        if self.is_production and (
            len(self.SECRET_KEY) < 32 or self.SECRET_KEY.startswith("change-me")
        ):
            raise RuntimeError("A random SECRET_KEY of at least 32 characters is required")
        if self.is_production and (
            self.DARAJA_SANDBOX_BASE_URL != "https://sandbox.safaricom.co.ke"
            or self.DARAJA_PRODUCTION_BASE_URL != "https://api.safaricom.co.ke"
        ):
            raise RuntimeError("Production Daraja endpoints must use Safaricom")
        provider = self.ENCRYPTION_PROVIDER.strip().lower()
        if provider not in {"local", "aws_kms"}:
            raise RuntimeError("ENCRYPTION_PROVIDER must be local or aws_kms")
        key_config = (
            self.ENCRYPTION_KMS_KEY_IDS_JSON if provider == "aws_kms" else self.ENCRYPTION_KEYS_JSON
        )
        if self.is_production and not key_config:
            raise RuntimeError(
                "The selected encryption provider requires versioned key configuration"
            )
        if key_config:
            try:
                keys = json.loads(key_config)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Encryption key configuration must be valid JSON") from exc
            if not isinstance(keys, dict) or self.ENCRYPTION_ACTIVE_KEY_ID not in keys:
                raise RuntimeError("ENCRYPTION_ACTIVE_KEY_ID must exist in key configuration")
        if self.is_production and not self.public_url.startswith("https://"):
            raise RuntimeError("PUBLIC_BASE_URL must be HTTPS in production")
        if self.mpesa_callback_hmac and not self.MPESA_WEBHOOK_SECRET:
            raise RuntimeError("MPESA_WEBHOOK_SECRET is required in hmac callback mode")
        if (
            self.is_production
            and not self.mpesa_callback_hmac
            and not self.MPESA_CALLBACK_IP_ALLOWLIST.strip()
        ):
            raise RuntimeError("Production callback IP allowlist cannot be empty")
        if self.MAX_CALLBACK_BODY_BYTES < 1024:
            raise RuntimeError("MAX_CALLBACK_BODY_BYTES must be at least 1024")
        if self.RATE_LIMIT_CALLBACK_VERIFIED_REQUESTS_PER_MINUTE < 60:
            raise RuntimeError("Verified callback rate limit must allow provider bursts")
        if self.RATE_LIMIT_CALLBACK_UNVERIFIED_REQUESTS_PER_MINUTE < 1:
            raise RuntimeError("Unverified callback rate limit must be positive")
        if self.EMAIL_DELIVERY_MODE not in {"outbox", "smtp"}:
            raise RuntimeError("EMAIL_DELIVERY_MODE must be outbox or smtp")
        if self.is_production and self.EMAIL_DELIVERY_MODE != "smtp":
            raise RuntimeError("Production requires an active SMTP email delivery provider")
        if self.is_production and self.EMAIL_DELIVERY_MODE == "smtp" and not self.SMTP_HOST:
            raise RuntimeError("SMTP_HOST is required for SMTP email delivery")
        if self.is_production and self.EMAIL_DELIVERY_MODE == "smtp" and not self.SMTP_STARTTLS:
            raise RuntimeError("Production SMTP delivery requires STARTTLS")
        if self.is_production and (not self.RATE_LIMIT_ENABLED or not self.REDIS_URL):
            raise RuntimeError("Production requires Redis-backed rate limiting")
        if self.is_production and not self.REQUIRE_PRIVILEGED_MFA:
            raise RuntimeError("Production requires MFA for privileged operations")
        if self.is_production and process_type == "api" and not self.ADMIN_DATABASE_URL:
            raise RuntimeError("Production API requires a dedicated platform-admin database role")
        if self.is_production and process_type == "worker" and not self.WORKER_DATABASE_URL:
            raise RuntimeError("Production worker requires a dedicated worker database role")
        if self.is_production and self.METRICS_ENABLED and not self.METRICS_BEARER_TOKEN:
            raise RuntimeError("METRICS_BEARER_TOKEN is required in production")
        if (
            self.is_production
            and process_type == "api"
            and self.METRICS_ENABLED
            and not self.METRICS_DATABASE_URL
        ):
            raise RuntimeError("METRICS_DATABASE_URL is required for RLS-safe production metrics")
        if self.is_production and process_type == "api":
            runtime_urls = [self.DATABASE_URL, self.ADMIN_DATABASE_URL]
            if self.METRICS_ENABLED:
                runtime_urls.append(self.METRICS_DATABASE_URL)
            if len(runtime_urls) != len(set(runtime_urls)):
                raise RuntimeError("Production runtime database roles must use distinct URLs")


settings = Settings()

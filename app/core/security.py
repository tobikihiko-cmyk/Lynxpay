"""Passwords, JWTs, encryption, and callback signature verification."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import Protocol
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_ALGORITHM = "HS256"


def hash_password(value: str) -> str:
    return pwd_context.hash(value)


def verify_password(value: str, digest: str) -> bool:
    return pwd_context.verify(value, digest)


def create_access_token(subject: str, session_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": secrets.token_hex(16),
        "sid": session_id,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "type", "iat", "exp", "jti"]},
        )
    except jwt.InvalidTokenError:
        return None
    return payload if payload.get("type") == "access" else None


def hash_opaque_token(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def generate_refresh_token() -> tuple[str, str, str]:
    prefix = f"lpr_{secrets.token_hex(6)}"
    token = f"{prefix}_{secrets.token_urlsafe(48)}"
    return token, prefix, hash_opaque_token(token)


def refresh_token_prefix(value: str) -> str | None:
    parts = value.split("_", 2)
    return "_".join(parts[:2]) if len(parts) == 3 and parts[0] == "lpr" else None


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, *, at_time: int | None = None) -> str:
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int(at_time if at_time is not None else time.time()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def verify_totp(secret: str, code: str, *, at_time: int | None = None) -> bool:
    return matching_totp_step(secret, code, at_time=at_time) is not None


def matching_totp_step(secret: str, code: str, *, at_time: int | None = None) -> int | None:
    now = int(at_time if at_time is not None else time.time())
    normalized = str(code).replace(" ", "")
    for offset in (-1, 0, 1):
        candidate_time = now + offset * 30
        if hmac.compare_digest(totp_code(secret, at_time=candidate_time), normalized):
            return candidate_time // 30
    return None


def totp_uri(secret: str, email: str) -> str:
    label = quote(f"{settings.MFA_ISSUER}:{email}")
    issuer = quote(settings.MFA_ISSUER)
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    )


def _legacy_fernet() -> Fernet:
    source = settings.SECRET_ENCRYPTION_KEY or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest())
    return Fernet(key)


class MasterKeyProvider(Protocol):
    """Wrap and unwrap one-time data keys without exposing stored plaintext secrets."""

    def wrap(self, key_id: str, data_key: bytes) -> bytes: ...

    def unwrap(self, key_id: str, wrapped_data_key: bytes) -> bytes: ...


class LocalKeyringProvider:
    """Environment keyring for development and self-hosting.

    The interface is intentionally compatible with a future KMS implementation: only
    encrypted data keys are persisted beside ciphertext.
    """

    def __init__(self) -> None:
        configured = json.loads(settings.ENCRYPTION_KEYS_JSON or "{}")
        if not isinstance(configured, dict):
            raise RuntimeError("ENCRYPTION_KEYS_JSON must contain a JSON object")
        if not configured and not settings.is_production:
            configured = {
                settings.ENCRYPTION_ACTIVE_KEY_ID: settings.SECRET_ENCRYPTION_KEY
                or settings.SECRET_KEY
            }
        self._keys = {str(key_id): str(value) for key_id, value in configured.items()}

    def _master(self, key_id: str) -> Fernet:
        source = self._keys.get(key_id)
        if not source:
            raise RuntimeError(f"Encryption key version {key_id!r} is unavailable")
        key = base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest())
        return Fernet(key)

    def wrap(self, key_id: str, data_key: bytes) -> bytes:
        return self._master(key_id).encrypt(data_key)

    def unwrap(self, key_id: str, wrapped_data_key: bytes) -> bytes:
        return self._master(key_id).decrypt(wrapped_data_key)


class AwsKmsProvider:
    """AWS KMS master-key provider; KMS only sees one-time data keys."""

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required when ENCRYPTION_PROVIDER=aws_kms") from exc
        configured = json.loads(settings.ENCRYPTION_KMS_KEY_IDS_JSON or "{}")
        if not isinstance(configured, dict):
            raise RuntimeError("ENCRYPTION_KMS_KEY_IDS_JSON must contain a JSON object")
        self._key_ids = {str(key_id): str(value) for key_id, value in configured.items()}
        kwargs = {"region_name": settings.AWS_REGION} if settings.AWS_REGION else {}
        self._client = boto3.client("kms", **kwargs)

    @staticmethod
    def _context(key_id: str) -> dict[str, str]:
        return {"service": "lynxpay", "key_version": key_id}

    def wrap(self, key_id: str, data_key: bytes) -> bytes:
        kms_key_id = self._key_ids.get(key_id)
        if not kms_key_id:
            raise RuntimeError(f"KMS key version {key_id!r} is unavailable")
        result = self._client.encrypt(
            KeyId=kms_key_id,
            Plaintext=data_key,
            EncryptionContext=self._context(key_id),
        )
        return base64.urlsafe_b64encode(result["CiphertextBlob"])

    def unwrap(self, key_id: str, wrapped_data_key: bytes) -> bytes:
        result = self._client.decrypt(
            CiphertextBlob=base64.urlsafe_b64decode(wrapped_data_key),
            EncryptionContext=self._context(key_id),
        )
        return result["Plaintext"]


def _key_provider() -> MasterKeyProvider:
    if settings.ENCRYPTION_PROVIDER.strip().lower() == "aws_kms":
        return AwsKmsProvider()
    return LocalKeyringProvider()


def is_encrypted_value(value: str | None) -> bool:
    return bool(value and value.startswith(("env1::", "enc::")))


def encryption_key_version(value: str | None) -> str:
    if value and value.startswith("env1::"):
        parts = value.split("::", 3)
        if len(parts) == 4:
            return parts[1]
    return "legacy"


def encrypt_sensitive_value(value: str | None) -> str | None:
    if not value or is_encrypted_value(value):
        return value
    key_id = settings.ENCRYPTION_ACTIVE_KEY_ID
    data_key = Fernet.generate_key()
    wrapped_data_key = _key_provider().wrap(key_id, data_key).decode()
    ciphertext = Fernet(data_key).encrypt(value.encode()).decode()
    return f"env1::{key_id}::{wrapped_data_key}::{ciphertext}"


def decrypt_sensitive_value(value: str | None) -> str | None:
    if not value or not is_encrypted_value(value):
        return None
    try:
        if value.startswith("enc::"):
            return _legacy_fernet().decrypt(value.removeprefix("enc::").encode()).decode()
        _, key_id, wrapped_data_key, ciphertext = value.split("::", 3)
        data_key = _key_provider().unwrap(key_id, wrapped_data_key.encode())
        return Fernet(data_key).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, RuntimeError, ValueError):
        return None


def reencrypt_sensitive_value(value: str | None) -> str | None:
    """Move a legacy or old-key ciphertext onto the currently active envelope key."""

    if not value:
        return None
    plaintext = decrypt_sensitive_value(value)
    if plaintext is None:
        raise ValueError("Sensitive value could not be decrypted")
    if encryption_key_version(value) == settings.ENCRYPTION_ACTIVE_KEY_ID:
        return value
    return encrypt_sensitive_value(plaintext)


def verify_callback_signature(body: bytes, signature: str | None) -> bool:
    if not settings.MPESA_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(settings.MPESA_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

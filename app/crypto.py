# -*- coding: utf-8 -*-
"""Application-level cryptography helpers.

This module provides:
  * Transparent encryption for PII columns (``EncryptedText``).
  * RFC 6238 TOTP generation/verification for MFA (no external dependency).
  * Backup-code hashing.

The encryption key is read from ``NORUS_ENCRYPTION_KEY``. Multiple comma
separated keys are supported for rotation: the first key encrypts, all keys
decrypt. In development a deterministic fallback key keeps local restarts
working; production refuses to start without an explicit key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

log = logging.getLogger(__name__)

PREFIX = "enc:v1:"
_DEV_FALLBACK = "norus-development-only-encryption-key-change-me"
_WARNED_DEV_KEY = False


def _environment() -> str:
    return os.environ.get("NORUS_ENV", "development").lower()


def _fernet_key(value: str) -> bytes:
    """Accept a Fernet key or derive one deterministically from a passphrase."""
    value = value.strip()
    if not value:
        raise ValueError("Clave de cifrado vacía.")
    try:
        Fernet(value.encode("utf-8"))
        return value.encode("utf-8")
    except Exception:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)


def _keys() -> list[bytes]:
    global _WARNED_DEV_KEY
    raw = os.environ.get("NORUS_ENCRYPTION_KEY", "").strip()
    if not raw:
        if _environment() == "production":
            raise RuntimeError(
                "NORUS_ENCRYPTION_KEY debe configurarse antes de iniciar en producción."
            )
        if not _WARNED_DEV_KEY:
            _WARNED_DEV_KEY = True
            log.warning(
                "NORUS_ENCRYPTION_KEY no configurada; se usa una clave de desarrollo. "
                "No utilice este valor en producción."
            )
        raw = _DEV_FALLBACK
    keys = [_fernet_key(part) for part in raw.split(",") if part.strip()]
    if not keys:
        raise RuntimeError("NORUS_ENCRYPTION_KEY no contiene claves válidas.")
    return keys


def _multi() -> MultiFernet:
    return MultiFernet([Fernet(k) for k in _keys()])


def encryption_active() -> bool:
    """True when an explicit production-grade key is configured."""
    return bool(os.environ.get("NORUS_ENCRYPTION_KEY", "").strip())


def encrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.startswith(PREFIX):
        return text
    token = _multi().encrypt(text.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text.startswith(PREFIX):
        # Legacy plaintext rows remain readable until rewritten.
        return text
    try:
        return _multi().decrypt(text[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        log.error("No se pudo descifrar un valor almacenado; revise la clave de cifrado.")
        return None


class EncryptedText(TypeDecorator):
    """SQLAlchemy column type that stores Fernet ciphertext at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_text(value)

    def process_result_value(self, value, dialect):
        return decrypt_text(value)


# ============================================================
# TOTP (RFC 6238) — sin dependencias externas
# ============================================================

TOTP_DIGITS = 6
TOTP_INTERVAL = 30


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _totp_at(secret: str, counter: int) -> str:
    try:
        key = base64.b32decode(secret, casefold=True)
    except Exception as exc:
        raise ValueError("Secreto TOTP inválido.") from exc
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:0{TOTP_DIGITS}d}"


def verify_totp(secret: str, code: str, window: int = 1) -> int | None:
    """Return the matching counter (for replay protection) or None."""
    if not secret or not code:
        return None
    candidate = "".join(ch for ch in str(code) if ch.isdigit())
    if len(candidate) != TOTP_DIGITS:
        return None
    current = int(time.time()) // TOTP_INTERVAL
    for offset in range(-window, window + 1):
        counter = current + offset
        if hmac.compare_digest(_totp_at(secret, counter), candidate):
            return counter
    return None


def totp_uri(secret: str, username: str, issuer: str = "Norus Intelligence") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{username}", safe="")
    params = {
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": str(TOTP_DIGITS),
        "period": str(TOTP_INTERVAL),
    }
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"otpauth://totp/{label}?{query}"


# ============================================================
# Códigos de respaldo
# ============================================================

def _pepper() -> bytes:
    return (
        os.environ.get("NORUS_JWT_SECRET")
        or os.environ.get("NORUS_ENCRYPTION_KEY")
        or _DEV_FALLBACK
    ).encode("utf-8")


def hash_backup_code(code: str) -> str:
    normalized = "".join(ch for ch in str(code).upper() if ch.isalnum())
    return hmac.new(_pepper(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_backup_codes(count: int = 10) -> list[str]:
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5).upper()
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def backup_codes_json(codes: list[str]) -> str:
    return encrypt_text(json.dumps([hash_backup_code(c) for c in codes])) or ""


def consume_backup_code(stored_encrypted: str | None, code: str) -> tuple[bool, str | None]:
    """Validate and remove a backup code. Returns (ok, new_encrypted_value)."""
    if not stored_encrypted or not code:
        return False, stored_encrypted
    try:
        hashes = json.loads(decrypt_text(stored_encrypted) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, stored_encrypted
    target = hash_backup_code(code)
    for index, item in enumerate(hashes):
        if hmac.compare_digest(str(item), target):
            hashes.pop(index)
            return True, encrypt_text(json.dumps(hashes))
    return False, stored_encrypted


def redact(value: str | None) -> str:
    """Small helper for logs: never print full PII."""
    if not value:
        return ""
    text = str(value)
    return text[:1] + "***" if len(text) > 1 else "***"

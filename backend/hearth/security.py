"""THE crypto module — every secret operation in Hearth goes through here.

Contract (docs/SECURITY.md): no other module imports argon2/Fernet/hashlib/
secrets. Persistence lives in adapters/app_db.py; these functions are pure.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_ph = PasswordHasher()  # argon2id, library defaults (memory-hard)


# ── passwords ──────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, stored_hash: str) -> tuple[bool, str | None]:
    """Returns (ok, new_hash_if_rehash_needed)."""
    try:
        _ph.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False, None
    if _ph.check_needs_rehash(stored_hash):
        return True, _ph.hash(plain)
    return True, None


# ── sessions & api tokens ──────────────────────────────────────────────────
def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def mint_session() -> tuple[str, str]:
    """(cookie_value, sha256_for_db)."""
    v = _secrets.token_urlsafe(32)
    return v, _sha256(v)


def verify_session(cookie_value: str, stored_sha256: str) -> bool:
    return hmac.compare_digest(_sha256(cookie_value), stored_sha256)


def mint_api_token(scope: str) -> tuple[str, str]:
    """('hrt_<43 urlsafe chars>', sha256_for_db). Plaintext shown ONCE."""
    v = "hrt_" + _secrets.token_urlsafe(32)
    return v, _sha256(v)


def verify_api_token(presented: str, stored_sha256: str) -> bool:
    return hmac.compare_digest(_sha256(presented), stored_sha256)


# ── third-party secrets at rest ────────────────────────────────────────────
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        from .config import settings

        key = HKDF(
            algorithm=hashes.SHA256(), length=32,
            salt=b"hearth-v1", info=b"hearth-connections",
        ).derive(settings.secret.encode())
        _fernet = Fernet(base64.urlsafe_b64encode(key))
    return _fernet


def encrypt_secret(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ── redaction ──────────────────────────────────────────────────────────────
def mask(value: str) -> str:
    """For logs and UI: 'hrt_a1b2****'. Never log unmasked secrets."""
    if not value:
        return ""
    return (value[:8] + "****") if len(value) > 12 else "****"

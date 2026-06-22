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


def mint_reset_token() -> tuple[str, str]:
    """One-time password-recovery token: ('hrt_reset_<urlsafe>', sha256_for_db).
    Generated out-of-band (the `hearth.recover` CLI) since there's no mail server;
    the plaintext is printed once to the operator and redeemed at /reset."""
    v = "hrt_reset_" + _secrets.token_urlsafe(24)
    return v, _sha256(v)


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


# ── TOTP 2FA (RFC 6238, stdlib only — no extra dependency) ──────────────────
def new_totp_secret() -> str:
    """A fresh base32 secret (160-bit) for authenticator apps."""
    return base64.b32encode(_secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    import struct
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    num = (int.from_bytes(h[o:o + 4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(num).zfill(digits)


def verify_totp(secret_b32: str, code: str, *, window: int = 1, step: int = 30) -> bool:
    """True if `code` matches the current time-step (±`window` for clock skew)."""
    import time
    code = (code or "").strip().replace(" ", "")
    if not (secret_b32 and code.isdigit()):
        return False
    counter = int(time.time() // step)
    return any(hmac.compare_digest(_hotp(secret_b32, counter + w), code.zfill(6))
               for w in range(-window, window + 1))


def totp_uri(secret_b32: str, account: str, issuer: str = "Hearth") -> str:
    """otpauth:// URI for QR enrollment in Google/Microsoft/Authy etc."""
    from urllib.parse import quote
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
            "&algorithm=SHA1&digits=6&period=30")


def new_recovery_codes(n: int = 8) -> list[str]:
    """Human-typable one-time backup codes, e.g. 'a1b2c3-d4e5f6' (48 bits each —
    brute-force-resistant even before the login backoff applies)."""
    return [f"{_secrets.token_hex(3)}-{_secrets.token_hex(3)}" for _ in range(n)]


def recovery_sha(code: str) -> str:
    return _sha256((code or "").strip().lower().replace(" ", ""))


# ── redaction ──────────────────────────────────────────────────────────────
def mask(value: str) -> str:
    """For logs and UI: 'hrt_a1b2****'. Never log unmasked secrets."""
    if not value:
        return ""
    return (value[:8] + "****") if len(value) > 12 else "****"


# ── email address validation (SMTP header-injection guard) ──────────────────
import re as _re

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(addr: str) -> bool:
    """True for a single, sane email address. Rejects anything with control
    characters (CR/LF/tab/NUL) or commas — the vectors for SMTP header
    injection and recipient smuggling — and requires a basic local@domain.tld
    shape. Deliberately strict, not RFC-complete: an address we'd refuse to send
    to is better than one that smuggles a Bcc header."""
    if not addr or len(addr) > 254:
        return False
    if any(c in addr for c in "\r\n\t\0,"):
        return False
    return bool(_EMAIL_RE.match(addr.strip()))

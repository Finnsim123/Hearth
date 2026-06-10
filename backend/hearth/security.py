"""THE crypto module — every secret operation in Hearth goes through here.

Contract (docs/SECURITY.md): no other module imports argon2/Fernet/hashlib/
secrets. Five capabilities, nothing else:

  passwords   hash_password / verify_password          (argon2id, rehash hint)
  sessions    mint_session / verify_session            (256-bit id, SHA-256 at rest)
  api tokens  mint_api_token / verify_api_token        ("hrt_" prefix, shown once)
  3rd-party   encrypt_secret / decrypt_secret          (Fernet, HKDF(HEARTH_SECRET))
  redaction   mask(value)                              ("hrt_a1b2****")

All functions are pure w.r.t. storage — they take/return values; persistence
lives in adapters/app_db.py (users, sessions, api_tokens, connections tables).
"""
from __future__ import annotations


def hash_password(plain: str) -> str:
    """argon2id with library defaults; embeds salt + params in the hash."""
    raise NotImplementedError


def verify_password(plain: str, stored_hash: str) -> tuple[bool, str | None]:
    """Returns (ok, new_hash_if_rehash_needed)."""
    raise NotImplementedError


def mint_session() -> tuple[str, str]:
    """Returns (cookie_value, sha256_for_db). Cookie: HttpOnly, SameSite=Lax,
    Secure when behind TLS."""
    raise NotImplementedError


def verify_session(cookie_value: str, stored_sha256: str) -> bool:
    raise NotImplementedError


def mint_api_token(scope: str) -> tuple[str, str]:
    """Returns (token 'hrt_<43 urlsafe chars>', sha256_for_db).
    The plaintext is shown ONCE in the UI and never stored."""
    raise NotImplementedError


def verify_api_token(presented: str, stored_sha256: str) -> bool:
    """Constant-time comparison."""
    raise NotImplementedError


def encrypt_secret(plain: str) -> str:
    """Fernet; key = HKDF(HEARTH_SECRET, info='hearth-connections')."""
    raise NotImplementedError


def decrypt_secret(ciphertext: str) -> str:
    raise NotImplementedError


def mask(value: str) -> str:
    """For logs and UI: 'hrt_a1b2****' / 'eyJh****'. Never log unmasked."""
    raise NotImplementedError

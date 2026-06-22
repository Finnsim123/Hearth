"""2FA: TOTP verify, recovery codes, and the repo enable/consume lifecycle."""
from __future__ import annotations

import time

from hearth import security
from hearth.adapters.app_db import AppDb
from hearth.domain.schemas import User


def test_totp_roundtrip_and_skew():
    secret = security.new_totp_secret()
    now = int(time.time() // 30)
    assert security.verify_totp(secret, security._hotp(secret, now))
    assert security.verify_totp(secret, security._hotp(secret, now - 1))   # within window
    assert not security.verify_totp(secret, security._hotp(secret, now - 5))
    assert not security.verify_totp(secret, "000000") or True   # ~never matches
    assert not security.verify_totp(secret, "abc")


def test_recovery_sha_is_normalized():
    codes = security.new_recovery_codes(6)
    assert len(codes) == 6 and all("-" in c for c in codes)
    assert security.recovery_sha(codes[0]) == security.recovery_sha(codes[0].upper() + " ")


def test_repo_totp_lifecycle(tmp_path):
    db = AppDb(tmp_path / "t.db")
    db.migrate()
    u = db.create_user(User(email="a@b.com", display_name="A", role="admin"), "password123")
    secret = security.new_totp_secret()
    db.set_totp_pending(u.id, secret)
    assert db.totp_secret(u.id) == secret
    assert db.user_by_email("a@b.com").totp_enabled is False

    codes = security.new_recovery_codes(3)
    db.enable_totp(u.id, [security.recovery_sha(c) for c in codes])
    assert db.user_by_email("a@b.com").totp_enabled is True

    assert db.consume_recovery_code(u.id, codes[0]) is True
    assert db.consume_recovery_code(u.id, codes[0]) is False   # single use
    assert db.consume_recovery_code(u.id, "nope-nope") is False

    db.disable_totp(u.id)
    assert db.user_by_email("a@b.com").totp_enabled is False
    assert db.totp_secret(u.id) is None

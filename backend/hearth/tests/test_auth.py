"""Auth enforcement: lockdown before setup, sessions after."""
from __future__ import annotations

import os

os.environ["HEARTH_NO_RESTART"] = "1"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARTH_DATA_DIR", str(tmp_path))
    import hearth.config as cfg
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    async def fake_probe(url, token):
        return {"reachable": True, "authed": True, "entities": 1,
                "version": "t", "timezone": "UTC", "error": None}
    async def fake_inventory(url, token):
        return []
    import hearth.adapters.ha_probe as hp
    monkeypatch.setattr(hp, "probe", fake_probe)
    monkeypatch.setattr(hp, "rest_inventory", fake_inventory)
    from hearth.main import create_app
    return TestClient(create_app())


PAYLOAD = {
    "account": {"name": "A", "email": "a@b.c", "password": "averylongpassword"},
    "ha": {"url": "http://x", "token": "t"},
    "influx": {"mode": "external", "url": "http://y", "org": "o", "token": "i",
               "sourceBucket": ""},
    "members": [{"name": "A", "personEntity": "", "hasDevice": True,
                 "notifyService": "", "avatar": "preset:ember"}],
    "llmKey": "", "taxonomyPreset": "minimal",
}


def test_lockdown_and_sessions(client):
    c = client
    # pre-setup: wizard endpoints open, everything else closed
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/persons").status_code == 401
    # private URL passes the SSRF guard; the probe just can't connect → 200 + error
    assert c.post("/api/ha/test", json={"url": "http://10.255.255.1:8123", "token": "y"}).status_code == 200

    # setup signs you in (cookie set on the response)
    r = c.post("/api/setup/complete", json=PAYLOAD)
    assert r.status_code == 200
    assert c.get("/api/auth/me").json()["role"] == "admin"
    assert c.get("/api/persons").status_code == 200

    # post-setup: wizard probe endpoints are no longer anonymous
    fresh = TestClient(c.app)
    assert fresh.post("/api/ha/test", json={}).status_code == 401
    assert fresh.get("/api/persons").status_code == 401

    # wrong + right login
    assert fresh.post("/api/auth/login",
                      json={"email": "a@b.c", "password": "nope"}).status_code == 401
    assert fresh.post("/api/auth/login",
                      json={"email": "a@b.c", "password": "averylongpassword"}).status_code == 200
    assert fresh.get("/api/auth/me").status_code == 200

    # logout kills the session server-side
    fresh.post("/api/auth/logout")
    assert fresh.get("/api/auth/me").status_code == 401


def test_recent_reset_token_rate_limit(tmp_path, monkeypatch):
    """recent_reset_token gates /auth/forgot so it can't mailbomb an inbox."""
    monkeypatch.setenv("HEARTH_SECRET", "test-secret")
    from hearth.adapters.app_db import AppDb
    from hearth.domain.schemas import User
    repo = AppDb(tmp_path / "rl.db")
    repo.migrate()
    u = repo.create_user(User(email="x@y.com", display_name="X"), "averylongpassword")
    assert repo.recent_reset_token(u.id) is False        # none yet
    repo.create_reset_token(u.id, "sha-abc", hours=1)
    assert repo.recent_reset_token(u.id, within_min=15) is True   # just minted
    assert repo.recent_reset_token(u.id, within_min=0) is False   # window elapsed


def test_2fa_enable_and_brute_force_lockout(client):
    """TOTP code attempts share the password lockout: once the password is known,
    the 6-digit code can't be brute-forced (wrong codes back off the account)."""
    import time
    from hearth import security
    c = client
    assert c.post("/api/setup/complete", json=PAYLOAD).status_code == 200
    email, pw = PAYLOAD["account"]["email"], PAYLOAD["account"]["password"]

    secret = c.post("/api/auth/2fa/setup").json()["secret"]
    code = security._hotp(secret, int(time.time() // 30))
    enabled = c.post("/api/auth/2fa/enable", json={"code": code}).json()
    assert enabled["ok"] and len(enabled["recovery_codes"]) >= 8

    # password alone now yields no session — second factor required
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.json().get("twofa_required") is True and "hearth_session" not in r.cookies

    # hammer wrong codes past the lockout threshold
    for _ in range(6):
        assert c.post("/api/auth/login",
                      json={"email": email, "password": pw, "code": "000000"}).status_code == 401
    # even a CORRECT code is now refused — the code path is rate-limited
    good = security._hotp(secret, int(time.time() // 30))
    assert c.post("/api/auth/login",
                  json={"email": email, "password": pw, "code": good}).status_code == 401

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

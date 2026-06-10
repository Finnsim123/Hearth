"""Setup-completion endpoint: persists everything, gated to first run."""
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
    # no live HA in tests: stub the probes
    async def fake_probe(url, token):
        return {"reachable": True, "authed": True, "entities": 3,
                "version": "t", "timezone": "Europe/Amsterdam", "error": None}
    async def fake_inventory(url, token):
        return [{"entity_id": "light.kitchen", "domain": "light", "friendly_name": None,
                 "device_class": None, "unit": None, "area": None, "disabled": False,
                 "state": "off"}]
    import hearth.adapters.ha_probe as hp
    monkeypatch.setattr(hp, "probe", fake_probe)
    monkeypatch.setattr(hp, "rest_inventory", fake_inventory)
    from hearth.main import create_app
    app = create_app()
    return TestClient(app), app.state.deps["repo"]


PAYLOAD = {
    "account": {"name": "Alex", "email": "a@b.c", "password": "averylongpassword"},
    "ha": {"url": "http://ha.local:8123", "token": "tok"},
    "influx": {"mode": "external", "url": "http://db:8086", "org": "homelab",
               "token": "itok", "sourceBucket": "homeassistant"},
    "members": [{"name": "Alex", "personEntity": "person.alex", "hasDevice": True,
                 "notifyService": "mobile_app_x", "avatar": "preset:ember"}],
    "llmKey": "",
    "taxonomyPreset": "standard",
}


def test_setup_complete_persists_everything(client):
    c, repo = client
    r = c.post("/api/setup/complete", json=PAYLOAD)
    assert r.status_code == 200 and r.json()["fasttrack"] is True
    assert repo.user_count() == 1
    assert repo.get_connection("ha")["token"] == "tok"
    influx = repo.get_connection("influx")
    assert influx["options"]["source_bucket"] == "homeassistant"
    assert repo.get_setting("timezone") == "Europe/Amsterdam"
    persons = repo.persons()
    assert persons[0].id == "alex" and persons[0].avatar == "preset:ember"
    slugs = {a.slug for a in repo.activities()}
    assert {"sleeping", "cooking", "movie"} <= slugs
    names = {b.name for b in repo.bindings()}
    assert "kitchen" in names and "alex_loc" in names      # heuristic + person binding
    assert repo.get_setting("fasttrack.pending")["source_bucket"] == "homeassistant"
    # second run refused
    assert c.post("/api/setup/complete", json=PAYLOAD).status_code == 409
    # health flips
    assert c.get("/api/health").json()["needs_setup"] is False


def test_setup_refuses_missing_password(client):
    c, repo = client
    bad = dict(PAYLOAD, account={"name": "A", "email": "a@b.c", "password": ""})
    assert c.post("/api/setup/complete", json=bad).status_code == 400
    assert repo.user_count() == 0          # nothing half-created

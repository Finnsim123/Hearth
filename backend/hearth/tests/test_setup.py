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
    # slow seeding is DEFERRED to the post-restart boot (the setup request
    # must answer instantly so the login cookie reaches the browser)
    assert repo.bindings() == []
    assert repo.get_setting("seed.pending")["members"][0]["name"] == "Alex"
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


import pytest


@pytest.mark.asyncio
async def test_post_restart_seed_creates_bindings_and_rules(client):
    c, repo = client
    assert c.post("/api/setup/complete", json=PAYLOAD).status_code == 200

    class FakeEvents:
        async def discover_entities(self):
            return [{"entity_id": "light.kitchen", "domain": "light",
                     "friendly_name": None, "device_class": None, "unit": None,
                     "area": "Kitchen", "disabled": False, "state": "off"},
                    {"entity_id": "binary_sensor.bed_left", "domain": "binary_sensor",
                     "friendly_name": "Bed left", "device_class": "occupancy",
                     "unit": None, "area": "Bedroom", "disabled": False, "state": "off"}]

    from hearth.domain.onboarding.seed import run_seed
    await run_seed(repo, FakeEvents())
    names = {b.name for b in repo.bindings()}
    assert "kitchen" in names and "alex_loc" in names
    alex = next(b for b in repo.bindings() if b.name == "alex_loc")
    assert alex.person_id == "alex"                       # the person_id fix
    assert len(repo.rules()) > 0
    assert repo.get_setting("seed.pending") is None       # cleared on success
    assert repo.get_setting("seed.status")["stage"] == "done"


def test_update_endpoints(client, tmp_path, monkeypatch):
    c, repo = client
    c.post("/api/setup/complete", json=PAYLOAD)        # signs us in
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("HEARTH_SHARED_DIR", str(shared))
    # no updater installed yet
    assert c.get("/api/system/update").json()["updater"] is False
    assert c.post("/api/system/update").status_code == 409
    # updater wrote a status file
    (shared / "update_status.json").write_text(
        '{"local": "abc", "remote": "def", "behind": 3, "latest_subject": "feat: x"}')
    st = c.get("/api/system/update").json()
    assert st["behind"] == 3 and st["updater"] is True
    # trigger drops the flag
    assert c.post("/api/system/update").json()["ok"] is True
    assert (shared / "update_requested").is_file()

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


def test_change_password_revokes_other_sessions(client):
    c, repo = client
    c.post("/api/setup/complete", json=PAYLOAD)        # auto-signs in
    email, pw = "a@b.c", "averylongpassword"

    # second "device"
    other = TestClient(c.app)
    other.post("/api/auth/login", json={"email": email, "password": pw})
    assert other.get("/api/auth/me").status_code == 200

    # wrong current password rejected; too-short new password rejected
    assert c.post("/api/auth/password",
                  json={"current": "nope", "new": "x" * 12}).status_code == 403
    assert c.post("/api/auth/password",
                  json={"current": pw, "new": "short"}).status_code == 400

    r = c.post("/api/auth/password",
               json={"current": pw, "new": "brand-new-password-456"})
    assert r.status_code == 200
    assert c.get("/api/auth/me").status_code == 200       # this browser stays in
    assert other.get("/api/auth/me").status_code == 401   # other device kicked
    assert c.post("/api/auth/login", json={
        "email": email, "password": "brand-new-password-456"}).status_code == 200


def test_api_token_bearer_scope(client):
    c, repo = client
    # wizard step 9 mints a token DURING setup (no users yet)
    tok = c.post("/api/tokens", json={"name": "Home Assistant"}).json()["token"]
    assert tok.startswith("hrt_")
    c.post("/api/setup/complete", json=PAYLOAD)

    fresh = TestClient(c.app)                       # no session cookie
    bearer = {"Authorization": f"Bearer {tok}"}
    # in-scope endpoints work with the bearer token
    assert fresh.get("/api/persons", headers=bearer).status_code == 200
    assert fresh.get("/api/predictions", headers=bearer).status_code == 200
    # feedback/action: authenticated, parses, 404s on unknown question
    r = fresh.post("/api/feedback/action", headers=bearer,
                   json={"action": "HEARTH_999_0"})
    assert r.status_code in (200, 404)
    # …but NOT without it (was anonymous before this change)
    assert fresh.post("/api/feedback/action", json={"action": "HEARTH_1_0"}).status_code == 401
    # …and out-of-scope endpoints are refused
    assert fresh.get("/api/models", headers=bearer).status_code == 403
    assert fresh.post("/api/tokens", headers=bearer, json={}).status_code == 403
    # bad token refused
    assert fresh.get("/api/persons",
                     headers={"Authorization": "Bearer hrt_nope"}).status_code == 403
    # revocation kills it
    tid = next(t["id"] for t in c.get("/api/tokens").json() if not t["revoked"])
    c.delete(f"/api/tokens/{tid}")
    assert fresh.get("/api/persons", headers=bearer).status_code == 403


def test_prune_empty_disables_zero_obs_but_keeps_person(client):
    c, repo = client
    c.post("/api/setup/complete", json=PAYLOAD)
    from hearth.domain.schemas import Binding, Role
    repo.save_binding(Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE, name="sofa"))
    repo.save_binding(Binding(entity_id="sensor.dead", role=Role.ENV, name="dead"))
    repo.save_binding(Binding(entity_id="person.alex", role=Role.PERSON, name="alex_loc"))

    class Tsdb:
        def raw_event_counts(self, names, days=7):
            return {"sofa": 5000, "dead": 0, "alex_loc": 0}
    repo_tsdb = c.app.state.deps
    repo_tsdb["tsdb"] = Tsdb()

    r = c.post("/api/bindings/prune-empty").json()
    assert r["disabled"] == 1 and r["names"] == ["dead"]   # person kept, sofa kept
    by_name = {b.name: b for b in repo.bindings()}
    assert by_name["dead"].enabled is False
    assert by_name["sofa"].enabled is True and by_name["alex_loc"].enabled is True


@pytest.mark.asyncio
async def test_inventory_sync_stages_new_updates_rooms_keeps_edits(client):
    c, repo = client
    c.post("/api/setup/complete", json=PAYLOAD)
    from hearth.domain.schemas import Binding, Role
    # existing binding the user has placed in a room
    repo.save_binding(Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE,
                              name="sofa", room="Living room"))

    class FakeEvents:
        async def discover_entities(self):
            return [
                # existing entity, AREA changed in HA → room should update
                {"entity_id": "binary_sensor.sofa", "domain": "binary_sensor",
                 "device_class": "occupancy", "unit": None, "area": "Lounge",
                 "disabled": False, "state": "off"},
                # brand-new bindable sensor → STAGED for approval, not bound
                {"entity_id": "sensor.kitchen_co2", "domain": "sensor",
                 "device_class": "carbon_dioxide", "unit": "ppm", "area": "Kitchen",
                 "disabled": False, "state": "600"},
                # junk / non-bindable → ignored
                {"entity_id": "button.restart", "domain": "button",
                 "device_class": None, "unit": None, "area": None,
                 "disabled": False, "state": "unknown"},
            ]

    from hearth.domain.onboarding.inventory_sync import (
        approve_pending_sensors, sync_inventory)
    res = await sync_inventory(repo, FakeEvents(), use_llm=False)
    assert res["pending"] == 1 and res["added"] == 0 and res["rooms_updated"] == 1
    by_eid = {b.entity_id: b for b in repo.bindings()}
    assert by_eid["binary_sensor.sofa"].room == "Lounge"       # area synced
    assert "sensor.kitchen_co2" not in by_eid                   # NOT auto-added
    pending = repo.get_setting("discovery.pending")
    assert [p["entity_id"] for p in pending] == ["sensor.kitchen_co2"]

    # approving binds it and clears the pending queue
    assert approve_pending_sensors(repo, ["sensor.kitchen_co2"]) == 1
    assert "sensor.kitchen_co2" in {b.entity_id for b in repo.bindings()}
    assert repo.get_setting("discovery.pending") == []


def test_factory_reset_returns_to_first_run(client):
    c, repo = client
    c.post("/api/setup/complete", json=PAYLOAD)
    assert c.get("/api/health").json()["needs_setup"] is False
    r = c.post("/api/system/reset", json={"wipe_data": False})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get("/api/health").json()["needs_setup"] is True   # no users → setup
    assert repo.persons() == [] and repo.bindings() == []

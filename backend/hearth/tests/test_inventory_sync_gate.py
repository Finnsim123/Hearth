"""Detect-then-ask inventory sync: new sensors are STAGED, not auto-added,
then bound only on approval (gap analysis E4). Runs without the full app."""
from __future__ import annotations

import pytest

from hearth.domain.onboarding.inventory_sync import (
    approve_pending_sensors, dismiss_pending_sensors, sync_inventory,
)
from hearth.domain.schemas import Binding, Role


class FakeRepo:
    def __init__(self, bindings=None):
        self._bindings = list(bindings or [])
        self.settings: dict = {}
    def bindings(self):
        return list(self._bindings)
    def save_binding(self, b):
        b.id = b.id or (len(self._bindings) + 1)
        self._bindings = [x for x in self._bindings if x.entity_id != b.entity_id]
        self._bindings.append(b)
        return b
    def persons(self):
        return []
    def get_setting(self, k, d=None):
        return self.settings.get(k, d)
    def set_setting(self, k, v):
        self.settings[k] = v


class FakeEvents:
    def __init__(self, inv):
        self._inv = inv
    async def discover_entities(self):
        return self._inv


INV = [
    # already bound, area changed in HA -> room should update
    {"entity_id": "binary_sensor.sofa", "domain": "binary_sensor",
     "device_class": "occupancy", "area": "Lounge", "disabled": False, "state": "off"},
    # brand-new bindable -> staged, NOT bound
    {"entity_id": "sensor.kitchen_co2", "domain": "sensor",
     "device_class": "carbon_dioxide", "unit": "ppm", "area": "Kitchen",
     "disabled": False, "state": "600"},
    # non-bindable junk -> ignored entirely
    {"entity_id": "button.restart", "domain": "button", "area": None,
     "disabled": False, "state": "unknown"},
]


@pytest.mark.asyncio
async def test_sync_stages_new_and_updates_rooms():
    repo = FakeRepo([Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE,
                             name="sofa", room="Living room")])
    res = await sync_inventory(repo, FakeEvents(INV), use_llm=False)

    assert res["rooms_updated"] == 1 and res["pending"] == 1 and res["added"] == 0
    bound = {b.entity_id: b for b in repo.bindings()}
    assert bound["binary_sensor.sofa"].room == "Lounge"      # area synced
    assert "sensor.kitchen_co2" not in bound                 # NOT auto-added
    pending = repo.get_setting("discovery.pending")
    assert [p["entity_id"] for p in pending] == ["sensor.kitchen_co2"]
    assert "button.restart" not in {p["entity_id"] for p in pending}   # junk skipped


def _repo_with_sofa():
    return FakeRepo([Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE,
                             name="sofa", room="Living room")])


@pytest.mark.asyncio
async def test_sync_is_idempotent_no_duplicate_pending():
    repo = _repo_with_sofa()
    await sync_inventory(repo, FakeEvents(INV), use_llm=False)
    await sync_inventory(repo, FakeEvents(INV), use_llm=False)
    pending = repo.get_setting("discovery.pending")
    assert [p["entity_id"] for p in pending] == ["sensor.kitchen_co2"]   # deduped


@pytest.mark.asyncio
async def test_approve_binds_and_clears_pending():
    repo = _repo_with_sofa()
    await sync_inventory(repo, FakeEvents(INV), use_llm=False)
    added = approve_pending_sensors(repo, ["sensor.kitchen_co2"])
    assert added == 1
    bound = {b.entity_id: b for b in repo.bindings()}
    assert "sensor.kitchen_co2" in bound and bound["sensor.kitchen_co2"].enabled is True
    assert bound["sensor.kitchen_co2"].role == Role.ENV
    assert repo.get_setting("discovery.pending") == []       # cleared


@pytest.mark.asyncio
async def test_dismiss_drops_without_binding():
    repo = _repo_with_sofa()
    await sync_inventory(repo, FakeEvents(INV), use_llm=False)
    remaining = dismiss_pending_sensors(repo, ["sensor.kitchen_co2"])
    assert remaining == 0
    assert "sensor.kitchen_co2" not in {b.entity_id for b in repo.bindings()}

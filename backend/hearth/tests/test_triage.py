"""Entity triage: LLM clusters drive the shortlist, with a presence safety floor."""
from __future__ import annotations

import pytest

from hearth.domain.onboarding.triage import triage_entities

INVENTORY = [
    {"entity_id": "light.kitchen", "friendly_name": "Kitchen light"},
    {"entity_id": "sensor.cpu_temp", "friendly_name": "CPU temperature"},
    {"entity_id": "binary_sensor.sofa", "friendly_name": "Sofa", "device_class": "occupancy"},
    {"entity_id": "person.alice", "friendly_name": "Alice"},
    {"entity_id": "sensor.printer_nozzle", "friendly_name": "Nozzle temp", "disabled": True},
]


class FakeRepo:
    def __init__(self):
        self.settings = {}
    def set_setting(self, k, v):
        self.settings[k] = v


class FakeAdvisor:
    async def cluster_entities(self, inventory):
        # deliberately OMITS person.alice — the floor must rescue it
        return [
            {"label": "Lights", "relevant": True, "why": "lights", "entities": ["light.kitchen"]},
            {"label": "Server diagnostics", "relevant": False, "why": "infra",
             "entities": ["sensor.cpu_temp"]},
            {"label": "Presence", "relevant": True, "entities": ["binary_sensor.sofa"]},
        ]


@pytest.mark.asyncio
async def test_triage_llm_keepset_plus_presence_floor():
    repo = FakeRepo()
    res = await triage_entities(repo, INVENTORY, FakeAdvisor())
    assert res["by"] == "llm"
    assert res["total"] == 4                      # disabled entity excluded
    kept = set(res["kept"])
    assert kept == {"light.kitchen", "binary_sensor.sofa", "person.alice"}
    assert "sensor.cpu_temp" not in kept          # irrelevant cluster dropped
    assert "person.alice" in kept                 # rescued by the safety floor
    assert repo.settings["entity_triage"]["kept_count"] == 3


@pytest.mark.asyncio
async def test_triage_heuristic_fallback_without_llm():
    repo = FakeRepo()
    res = await triage_entities(repo, INVENTORY, advisor=None)
    assert res["by"] == "heuristic"
    kept = set(res["kept"])
    # role-positive entities kept; blocklisted CPU temp ignored
    assert {"light.kitchen", "person.alice", "binary_sensor.sofa"} <= kept
    assert "sensor.cpu_temp" not in kept
    # clusters are canonical categories with stable keys + icons
    cats = {c["category"] for c in res["clusters"]}
    assert {"lights", "presence"} <= cats
    assert all(c.get("icon") for c in res["clusters"])
    # the blocklisted entity lands in a not-relevant category, never the keep-set
    not_relevant = [c for c in res["clusters"] if not c["relevant"]]
    assert any("sensor.cpu_temp" in c["entities"] for c in not_relevant)

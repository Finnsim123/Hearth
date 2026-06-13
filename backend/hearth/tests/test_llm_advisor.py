"""LLM advisor: validation is the contract — garbage in, floor out."""
from __future__ import annotations

import json

import pytest

from hearth.adapters.openrouter_llm import (
    OpenRouterAdvisor, allowed_features, validate_predicate,
)
from hearth.domain.schemas import Activity, Binding, Role

BINDINGS = [Binding(entity_id="sensor.matras_links", role=Role.BED, name="bed_links"),
            Binding(entity_id="person.alice", role=Role.PERSON, name="alice_loc",
                    person_id="alice")]
ACTS = [Activity(slug="sleeping", name="Sleeping"), Activity(slug="home", name="Home")]


class FakeRepo:
    def get_connection(self, kind):
        return {"url": "http://x", "token": "t", "options": {}}


def _advisor(monkeypatch, canned):
    adv = OpenRouterAdvisor(FakeRepo())
    async def fake_chat(system, user, max_tokens=4000, **kwargs):
        return canned
    monkeypatch.setattr(adv, "_chat", fake_chat)
    return adv


def test_extract_json_salvages_truncated_array():
    from hearth.adapters.openrouter_llm import _extract_json
    # a response cut off mid-object (max_tokens) — keep the complete elements
    truncated = '[{"a": 1}, {"b": 2}, {"c": 3'
    out = _extract_json(truncated)
    assert out == [{"a": 1}, {"b": 2}]


def test_validate_predicate_whitelist():
    feats = allowed_features(BINDINGS)
    assert "bed_links_occupied" in feats and "alice_loc_home_last" in feats
    good = {"all": [{"feat": "bed_links_occupied", "op": "==", "value": 1},
                    {"any": [{"feat": "hour_of_day", "op": ">=", "value": 22}]}]}
    assert validate_predicate(good, feats)
    assert not validate_predicate({"feat": "os.system", "op": "==", "value": 1}, feats)
    assert not validate_predicate({"feat": "bed_links_occupied", "op": "LIKE", "value": 1}, feats)
    assert not validate_predicate({"feat": "bed_links_occupied", "op": "==",
                                   "value": "x"}, feats)


@pytest.mark.asyncio
async def test_propose_bindings_validates_and_dedupes(monkeypatch):
    adv = _advisor(monkeypatch, [
        {"entity_id": "sensor.matras_links", "role": "bed", "name": "Bed Links!!", "room": "Slaapkamer"},
        {"entity_id": "sensor.matras_links", "role": "bed", "name": "bed_links"},   # dupe entity ok, name dedup
        {"entity_id": "sensor.unknown", "role": "bed", "name": "ghost"},            # not in inventory
        {"entity_id": "sensor.matras_rechts", "role": "spaceship", "name": "x"},    # bad role
    ])
    inventory = [{"entity_id": "sensor.matras_links", "disabled": False},
                 {"entity_id": "sensor.matras_rechts", "disabled": False}]
    out = await adv.propose_bindings(inventory)
    assert len(out) == 1
    assert out[0].role is Role.BED and out[0].name == "bed_links" and out[0].room == "Slaapkamer"


@pytest.mark.asyncio
async def test_propose_rules_drops_invalid(monkeypatch):
    adv = _advisor(monkeypatch, [
        {"activity": "sleeping", "person": "alice", "priority": 20,
         "predicate": {"all": [{"feat": "bed_links_occupied", "op": "==", "value": 1}]}},
        {"activity": "hacking", "priority": 20,                                  # unknown slug
         "predicate": {"all": [{"feat": "bed_links_occupied", "op": "==", "value": 1}]}},
        {"activity": "sleeping", "priority": 20,
         "predicate": {"all": [{"feat": "__import__", "op": "==", "value": 1}]}},  # alien feat
    ])
    out = await adv.propose_rules(BINDINGS, ACTS)
    assert len(out) == 1 and out[0].activity_slug == "sleeping" and out[0].person_id == "alice"


@pytest.mark.asyncio
async def test_annotate_windows_bounds(monkeypatch):
    adv = _advisor(monkeypatch, [{"i": 0, "label": "sleeping", "confidence": 0.9},
                                 {"i": 1, "label": "flying", "confidence": 0.9},
                                 {"i": 99, "label": "home", "confidence": 0.5}])
    res = await adv.annotate_windows([{"h": 2}, {"h": 3}], ACTS)
    assert res[0] == ("sleeping", 0.9)
    assert res[1] == (None, 0.0)          # invalid label dropped


@pytest.mark.asyncio
async def test_chat_emits_sending_then_received_activity(monkeypatch):
    """The live Welcome screen reads llm.activity — _chat must write 'sending'
    before the request and 'received' (with item count) after the response."""
    events = []

    class RecRepo(FakeRepo):
        def set_setting(self, key, value):
            if key == "llm.activity":
                events.append(value)

    adv = OpenRouterAdvisor(RecRepo())

    class FakeResp:
        status = 200
        async def text(self):
            return json.dumps({"choices": [{"message": {"content": "[1, 2, 3]"},
                                            "finish_reason": "stop"}],
                               "usage": {"completion_tokens": 7}})
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class FakeSession:
        def post(self, *a, **k): return FakeResp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("hearth.adapters.openrouter_llm.aiohttp.ClientSession",
                        lambda *a, **k: FakeSession())

    out = await adv._chat("sys", "usr", task="Mapping sensors to roles",
                          sent="3 entities")
    assert out == [1, 2, 3]
    assert [e["phase"] for e in events] == ["sending", "received"]
    assert events[0]["sent"] == "3 entities"
    assert events[0]["prompt"] == "usr"        # prompt preview for the transcript
    assert events[1]["items"] == 3
    assert events[1]["reply"] == "[1, 2, 3]"   # reply preview for the transcript


@pytest.mark.asyncio
async def test_chat_accumulates_usage(monkeypatch):
    """Each successful call adds to llm.usage (calls + tokens + running cost) —
    the Settings usage counter reads this."""
    store: dict = {}

    class UsageRepo(FakeRepo):
        def get_setting(self, key): return store.get(key)
        def set_setting(self, key, value): store[key] = value

    adv = OpenRouterAdvisor(UsageRepo())

    class FakeResp:
        status = 200
        async def text(self):
            return json.dumps({"choices": [{"message": {"content": "[1]"},
                                            "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class FakeSession:
        def post(self, *a, **k): return FakeResp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("hearth.adapters.openrouter_llm.aiohttp.ClientSession",
                        lambda *a, **k: FakeSession())

    await adv._chat("s", "u", task="t")
    await adv._chat("s", "u", task="t")
    u = store["llm.usage"]
    assert u["calls"] == 2
    assert u["input_tokens"] == 200 and u["output_tokens"] == 100
    assert u["est_usd"] > 0 and u["since"] and u["last_at"]


@pytest.mark.asyncio
async def test_chat_failure_degrades_to_empty(monkeypatch):
    adv = OpenRouterAdvisor(FakeRepo())
    async def boom(system, user, max_tokens=4000, **kwargs):
        raise RuntimeError("api down")
    monkeypatch.setattr(adv, "_chat", boom)
    assert await adv.propose_rules(BINDINGS, ACTS) == []
    assert await adv.propose_taxonomy([]) == []

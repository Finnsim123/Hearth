"""OpenRouterAdvisor.propose_feature_spec orchestration, with a fake LLM (no net)."""
from __future__ import annotations

import pytest

from hearth.adapters.openrouter_llm import DEFAULT_MODEL, OpenRouterAdvisor, choose_model


def test_choose_model_precedence():
    # user's explicit choice always wins
    assert choose_model("anthropic/claude-sonnet-4.6", "openai/gpt-4o") == "anthropic/claude-sonnet-4.6"
    # unset or 'auto' -> the per-task fallback
    assert choose_model(None, "openai/gpt-4o") == "openai/gpt-4o"
    assert choose_model("auto", "openai/gpt-4o") == "openai/gpt-4o"
    assert choose_model("", "") == DEFAULT_MODEL


class FakeRepo:
    def persons(self):
        return []
    def get_connection(self, kind):
        return {"options": {"model": "test/model"}}


@pytest.mark.asyncio
async def test_propose_feature_spec_orchestration(monkeypatch):
    adv = OpenRouterAdvisor(FakeRepo())

    # canned responses per _chat call, in order: selection, features, composites
    responses = [
        [{"entity_id": "binary_sensor.sofa", "keep": True, "role": "presence",
          "info_tier": "T1", "reliability": "ok", "reason": "couch"},
         {"entity_id": "media_player.tv", "keep": True, "role": "media",
          "info_tier": "T2", "reliability": "ok", "reason": "tv"}],
        [{"name": "sofa_occ", "transform": "occupancy_fraction",
          "inputs": ["binary_sensor.sofa"], "info_tier": "T1",
          "rationale": "sofa time", "expected_separates": ["movie"]},
         {"name": "tv_on", "transform": "last_state", "inputs": ["media_player.tv"],
          "info_tier": "T2", "rationale": "tv state"}],
        [{"name": "movie_combo", "transform": "co_occurrence_and",
          "inputs": ["sofa_occ", "tv_on"], "params": {"threshold": 0.5},
          "rationale": "sofa+tv = movie", "expected_separates": ["movie"]}],
    ]
    calls = {"n": 0}

    async def fake_chat(system, user, max_tokens=4000, model=None):
        out = responses[calls["n"]]
        calls["n"] += 1
        return out

    monkeypatch.setattr(adv, "_chat", fake_chat)

    catalog = [
        {"entity_id": "binary_sensor.sofa", "metadata": {"domain": "binary_sensor"},
         "stats": {"value_type": "boolean", "distinct_values": 2, "flatline_frac": 0.1}},
        {"entity_id": "media_player.tv", "metadata": {"domain": "media_player"},
         "stats": {"value_type": "enum", "distinct_values": 3}},
    ]
    spec = await adv.propose_feature_spec(catalog, ["movie", "cooking"], mode="conservative")

    assert calls["n"] == 3                                   # all three passes ran
    assert {s.entity_id for s in spec.selections} == {"binary_sensor.sofa", "media_player.tv"}
    names = [f.name for f in spec.features]
    assert "sofa_occ" in names and "movie_combo" in names    # composite references survived
    assert spec.llm_model == "test/model" and spec.created_by == "llm"


@pytest.mark.asyncio
async def test_propose_feature_spec_degrades_on_chat_failure(monkeypatch):
    adv = OpenRouterAdvisor(FakeRepo())

    async def boom(system, user, max_tokens=4000, model=None):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(adv, "_chat", boom)
    spec = await adv.propose_feature_spec(
        [{"entity_id": "binary_sensor.sofa", "metadata": {"domain": "binary_sensor"}}],
        ["movie"], mode="conservative")
    # total LLM outage -> empty but valid spec, never a crash
    assert spec.selections == [] and spec.features == []

"""LLM cluster-name suggestions: the adapter validates the model's reply —
whitelists slugs, clamps confidence, derives kind, drops junk — and never
raises (manual naming must always survive)."""
from __future__ import annotations

import pytest

from hearth.adapters.openrouter_llm import OpenRouterAdvisor
from hearth.domain.schemas import Activity, ClusterCard


class FakeRepo:
    def get_setting(self, k, d=None): return d
    def get_connection(self, k): return {"url": "x", "token": "y", "options": {}}


ACTS = [Activity(slug="relaxing", name="Relaxing"), Activity(slug="cooking", name="Cooking")]
EV = {"summary": "Weekday afternoons", "plain": [{"label": "Bed empty", "dir": "down"}],
      "when": {"daypart": "afternoon"}, "where": ["Bedroom"]}


def _advisor(reply):
    adv = OpenRouterAdvisor(FakeRepo())

    async def fake_chat(system, user, **kw):
        return reply
    adv._chat = fake_chat                       # type: ignore[assignment]
    return adv


@pytest.mark.asyncio
async def test_parses_and_validates_suggestions():
    reply = {"suggestions": [
        {"name": "Relaxing", "slug": "relaxing", "rationale": "sofa, afternoon", "confidence": 0.8},
        {"name": "Afternoon nap", "slug": None, "rationale": "bed empty? no", "confidence": 1.7},
        {"name": "Bogus", "slug": "not_a_real_slug", "rationale": "x", "confidence": "high"},
        {"name": "", "slug": None},             # dropped: no name
    ]}
    out = await _advisor(reply).suggest_cluster_names(ClusterCard(), EV, ACTS)
    assert len(out) == 3
    assert out[0] == {"name": "Relaxing", "slug": "relaxing", "rationale": "sofa, afternoon",
                      "confidence": 0.8, "kind": "existing"}
    assert out[1]["kind"] == "new" and out[1]["confidence"] == 1.0      # clamped
    assert out[2]["slug"] is None and out[2]["confidence"] == 0.5       # bad slug + conf


@pytest.mark.asyncio
async def test_bad_shapes_return_empty_not_raise():
    assert await _advisor({"nope": 1}).suggest_cluster_names(ClusterCard(), EV, ACTS) == []
    assert await _advisor("not a dict").suggest_cluster_names(ClusterCard(), EV, ACTS) == []

    adv = OpenRouterAdvisor(FakeRepo())
    async def boom(*a, **k): raise RuntimeError("rate limited")
    adv._chat = boom                            # type: ignore[assignment]
    assert await adv.suggest_cluster_names(ClusterCard(), EV, ACTS) == []

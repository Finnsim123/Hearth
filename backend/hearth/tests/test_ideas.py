"""Bespoke idea suggestion: JSON parsing, validation, and graceful empties."""
from __future__ import annotations

import asyncio

from hearth.domain.onboarding.ideas import suggest_ideas


class FakeAdv:
    def __init__(self, out): self.out = out
    async def _chat(self, system, user, **k): return self.out


def test_parses_and_validates():
    adv = FakeAdv({"ideas": [{"title": "Movie mode", "text": "dim the lights"},
                             {"title": "", "text": "missing title"},
                             {"nope": 1}]})
    out = asyncio.run(suggest_ideas(adv, ["lights", "media"]))
    assert out == [{"title": "Movie mode", "text": "dim the lights"}]


def test_no_advisor_or_cats():
    assert asyncio.run(suggest_ideas(None, ["lights"])) == []
    assert asyncio.run(suggest_ideas(FakeAdv({"ideas": []}), [])) == []


def test_bad_shape_is_empty():
    assert asyncio.run(suggest_ideas(FakeAdv("not json"), ["lights"])) == []
    assert asyncio.run(suggest_ideas(FakeAdv({"x": 1}), ["lights"])) == []

from __future__ import annotations

from pathlib import Path

import pytest

from hearth.adapters.app_db import AppDb
from hearth.domain.labeling.palette import (
    DEFAULT_COLOR, PALETTE, WELL_KNOWN, ensure_colors, is_unset, pick_color,
)
from hearth.domain.schemas import Activity


@pytest.fixture
def db(tmp_path: Path) -> AppDb:
    db = AppDb(tmp_path / "test.db")
    db.migrate()
    return db


def test_is_unset():
    assert is_unset("") and is_unset(None) and is_unset(DEFAULT_COLOR)
    assert not is_unset("#34d399")


def test_pick_color_prefers_well_known():
    assert pick_color("home", set()) == WELL_KNOWN["home"]
    # well-known wins even if the colour is already used elsewhere
    assert pick_color("sleeping", {WELL_KNOWN["sleeping"]}) == WELL_KNOWN["sleeping"]


def test_pick_color_first_free_then_stable_hash():
    used = {PALETTE[0]}
    assert pick_color("gaming", used) == PALETTE[1]         # first free
    allused = {c for c in PALETTE}
    # everything taken → deterministic slot, stable across calls
    assert pick_color("gaming", allused) == pick_color("gaming", allused)


def test_save_activity_autoassigns_distinct_colors(db):
    a = db.save_activity(Activity(slug="home", name="Home"))            # sentinel default
    b = db.save_activity(Activity(slug="gaming", name="Gaming"))
    assert a.color == WELL_KNOWN["home"]
    assert not is_unset(b.color)
    assert b.color != a.color                                           # distinct


def test_save_activity_respects_explicit_color(db):
    a = db.save_activity(Activity(slug="reading", name="Reading", color="#123456"))
    assert a.color == "#123456"


def test_ensure_colors_idempotent_and_preserves_explicit(db):
    # save_activity already auto-colours on insert, so a healthy set needs no
    # backfill; ensure_colors must be a no-op and never touch explicit colours.
    db.save_activity(Activity(slug="home", name="Home"))
    db.save_activity(Activity(slug="custom", name="Custom", color="#abcdef"))
    assert ensure_colors(db) == 0
    assert all(not is_unset(a.color) for a in db.activities())
    assert next(a for a in db.activities() if a.slug == "custom").color == "#abcdef"

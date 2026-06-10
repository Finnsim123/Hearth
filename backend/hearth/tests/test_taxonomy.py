"""Hierarchy (LCPN): coarse projection, fine sub-problems, backfill."""
from __future__ import annotations

import pandas as pd

from hearth.domain.labeling.taxonomy import (
    children_of, ensure_hierarchy, fine_label_series, parent_map,
    parents_with_children, to_coarse)
from hearth.domain.schemas import Activity

ACTS = [
    Activity(id=1, slug="sleeping", name="Sleeping"),
    Activity(id=2, slug="home", name="Home"),
    Activity(id=3, slug="away", name="Away"),
    Activity(id=4, slug="cooking", name="Cooking", parent_id=2),
    Activity(id=5, slug="eating", name="Eating", parent_id=2),
    Activity(id=6, slug="movie", name="Movie", parent_id=2),
]


def test_coarse_projection():
    pmap = parent_map(ACTS)
    assert to_coarse("eating", pmap) == "home"      # home AND eating: both true
    assert to_coarse("home", pmap) == "home"
    assert to_coarse("away", pmap) == "away"
    assert to_coarse("unknown_slug", pmap) == "unknown_slug"   # graceful


def test_children_and_parents():
    assert set(children_of("home", ACTS)) == {"cooking", "eating", "movie"}
    assert parents_with_children(ACTS) == ["home"]


def test_fine_label_series_projects_one_subproblem():
    pmap = parent_map(ACTS)
    labels = pd.Series(["sleeping", "home", "cooking", "away", "eating"])
    fine = fine_label_series(labels, "home", pmap)
    # sleeping/away are not home's business; "home" = the unspecified class
    assert fine.tolist() == [None, "home", "cooking", None, "eating"]


def test_ensure_hierarchy_backfills_known_slugs():
    class Repo:
        def __init__(self):
            self.acts = [Activity(id=1, slug="home", name="Home"),
                         Activity(id=2, slug="cooking", name="Cooking"),
                         Activity(id=3, slug="gym", name="Gym")]   # unknown → untouched
        def activities(self): return self.acts
        def save_activity(self, a):
            return a
    repo = Repo()
    assert ensure_hierarchy(repo) == 1
    assert repo.acts[1].parent_id == 1 and repo.acts[2].parent_id is None

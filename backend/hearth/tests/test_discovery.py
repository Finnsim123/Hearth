"""Pattern discovery: synthetic two-cluster data → cards with sane signatures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from hearth.domain.discovery.clustering import (
    _is_duplicate, discover_person, run_discovery, signature)
from hearth.domain.schemas import ClusterCard, Person


def _synthetic_features(n=600, seed=7) -> pd.DataFrame:
    """Two distinct behaviors + noise: 'movie' evenings (sofa+media) and
    'gym' mornings (away+steps), on a 30-min grid."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 5, 10, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(minutes=30 * i) for i in range(n)])
    df = pd.DataFrame({
        "sofa_presence_frac": rng.normal(0.05, 0.03, n).clip(0, 1),
        "media_playing": np.zeros(n),
        "steps_delta": rng.normal(20, 10, n).clip(0),
        "home_frac": np.ones(n) * 0.9,
        "kitchen_presence_frac": rng.normal(0.05, 0.03, n).clip(0, 1),
        "constant_junk": np.ones(n),                   # must be dropped
    }, index=idx)
    movie = (idx.hour >= 20) & (idx.hour <= 22)
    df.loc[movie, "sofa_presence_frac"] = rng.normal(0.9, 0.05, movie.sum()).clip(0, 1)
    df.loc[movie, "media_playing"] = 1.0
    gym = (idx.hour >= 7) & (idx.hour <= 8)
    df.loc[gym, "home_frac"] = 0.1
    df.loc[gym, "steps_delta"] = rng.normal(2000, 300, gym.sum())
    return df


class FakeTsdb:
    def __init__(self, feats):
        self.feats = feats
        self.labels_written = []

    def read_features(self, person, fset, start, end):
        return self.feats

    def read_labels(self, person, start, end):
        return []

    def write_label(self, ev):
        self.labels_written.append(ev)


class FakeRepo:
    def __init__(self):
        self.settings = {"timezone": "UTC", "composites": []}
        self.saved: list[ClusterCard] = []

    def get_setting(self, k, d=None): return self.settings.get(k, d)
    def persons(self): return [Person(id="alice", name="Alice")]
    def clusters(self, status=None, person_id=None):
        return [c for c in self.saved if (status is None or c.status == status)]
    def clear_clusters(self, person_id, status="new"):
        self.saved = [c for c in self.saved if c.status != status]
    def save_cluster(self, c):
        c.id = len(self.saved) + 1
        self.saved.append(c)
        return c


def test_discovery_finds_the_planted_patterns():
    repo, tsdb = FakeRepo(), FakeTsdb(_synthetic_features())
    cards = run_discovery(tsdb, repo, days=30)
    assert len(cards) >= 2
    # the movie cluster: sofa & media must dominate a signature
    sig_feats = [{f for f, _ in c.signature[:3]} for c in cards]
    assert any({"sofa_presence_frac", "media_playing"} <= s for s in sig_feats)
    # constant feature never appears in any signature
    assert all("constant_junk" not in {f for f, _ in c.signature} for c in cards)
    # histograms concentrate where we planted the behavior
    movie_card = next(c for c in cards
                      if any(f == "media_playing" and z > 0 for f, z in c.signature[:3]))
    evening = sum(movie_card.hour_histogram[20:23])
    assert evening > sum(movie_card.hour_histogram) * 0.8
    assert movie_card.person_id == "alice" and movie_card.n_windows >= 10
    assert len(movie_card.example_windows) == movie_card.n_windows


def test_too_little_data_is_skipped():
    repo = FakeRepo()
    cards = run_discovery(FakeTsdb(_synthetic_features(n=50)), repo, days=30)
    assert cards == []


def test_dedupe_against_handled_cards():
    sig = [("sofa_presence_frac", 4.0), ("media_playing", 3.5),
           ("home_frac", 1.2), ("steps_delta", -0.8)]
    handled = [ClusterCard(person_id="alice", status="dismissed",
                           signature=[("media_playing", 3.0), ("sofa_presence_frac", 2.8),
                                      ("home_frac", 1.0), ("kitchen_presence_frac", 0.5)])]
    assert _is_duplicate(sig, handled) is True
    other = [("co2_delta", 3.0), ("kitchen_presence_frac", 2.5),
             ("steps_delta", 1.0), ("humidity_mean", 0.9)]
    assert _is_duplicate(other, handled) is False


def test_rerun_replaces_new_but_keeps_handled():
    repo, tsdb = FakeRepo(), FakeTsdb(_synthetic_features())
    first = run_discovery(tsdb, repo, days=30)
    assert len(repo.clusters(status="new")) == len(first)
    # name one → it survives the next run; new pile is replaced not duplicated
    named = repo.clusters(status="new")[0]
    named.status, named.named_activity_slug = "named", "movie"
    again = run_discovery(tsdb, repo, days=30)
    statuses = [c.status for c in repo.saved]
    assert statuses.count("named") == 1
    assert len([s for s in statuses if s == "new"]) == len(again)

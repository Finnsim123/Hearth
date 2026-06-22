"""Co-activation clustering: two sensor groups that fire together → two clusters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from hearth.domain.discovery.coactivation import _activity, cluster_sensors


def _synthetic_raw(n=2000, seed=11) -> pd.DataFrame:
    """Two co-firing groups on a 1-min grid: a 'morning route' (hall+stairs that
    move together) and an 'evening cook' (hob+extractor that move together),
    each active in disjoint hours so the groups don't correlate with each other."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 5, 10, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(minutes=i) for i in range(n)])
    hour = idx.hour
    morning = (hour >= 7) & (hour <= 8)
    evening = (hour >= 18) & (hour <= 19)

    def fires(mask, p):                              # a step-changing series when active
        f = (rng.random(n) < np.where(mask, p, 0.002)).astype(float)
        return np.cumsum(f)                          # monotone → diff() marks the fires

    return pd.DataFrame({
        "hall": fires(morning, 0.5), "stairs": fires(morning, 0.5),
        "hob": fires(evening, 0.5), "extractor": fires(evening, 0.5),
    }, index=idx)


def test_activity_counts_moves_per_bin():
    raw = _synthetic_raw()
    act = _activity(raw)
    assert list(act.columns) == list(raw.columns)
    assert (act >= 0).all().all()
    # morning sensors light up in the morning bins, hob does not
    morning_bins = (act.index.hour >= 7) & (act.index.hour <= 8)
    assert act.loc[morning_bins, "hall"].sum() > act.loc[morning_bins, "hob"].sum()


def test_clusters_separate_the_two_groups():
    out = cluster_sensors(_synthetic_raw())
    assign = out["assign"]
    assert set(assign) == {"hall", "stairs", "hob", "extractor"}
    # sensors that fire together share a cluster; the two groups are split
    assert assign["hall"] == assign["stairs"]
    assert assign["hob"] == assign["extractor"]
    assert assign["hall"] != assign["hob"]
    assert len(out["clusters"]) == 2
    # layout seeds are normalized to 0..1
    for c in out["clusters"]:
        assert 0.0 <= c["x"] <= 1.0 and 0.0 <= c["y"] <= 1.0


def test_too_few_sensors_returns_empty():
    raw = _synthetic_raw().iloc[:, :2]
    assert cluster_sensors(raw) == {"clusters": [], "assign": {}}


def test_empty_frame_is_safe():
    assert cluster_sensors(pd.DataFrame()) == {"clusters": [], "assign": {}}

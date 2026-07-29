"""Confident-learning label-error flags: detection, ledger, weights, re-asks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from hearth.domain.training.label_quality import (SUSPECT_WEIGHT,
                                                  confident_flags, mark_asked,
                                                  suspect_multipliers,
                                                  suspects_to_ask,
                                                  update_suspects)

T0 = datetime(2026, 7, 20, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self):
        self.settings: dict = {}
    def get_setting(self, key, default=None):
        return self.settings.get(key, default)
    def set_setting(self, key, value):
        self.settings[key] = value


def _pooled(n_per_class=20, n_bad=3):
    """Two clean classes + n_bad rows labeled 'cook' that the model confidently
    scores as 'away' — the classic mislabel signature."""
    idx, y, p_cook, p_away = [], [], [], []
    for i in range(n_per_class):
        idx.append(T0 + timedelta(minutes=30 * i))
        y.append("cook"); p_cook.append(0.85); p_away.append(0.15)
    for i in range(n_per_class):
        idx.append(T0 + timedelta(days=1, minutes=30 * i))
        y.append("away"); p_cook.append(0.1); p_away.append(0.9)
    for i in range(n_bad):
        idx.append(T0 + timedelta(days=2, minutes=30 * i))
        y.append("cook"); p_cook.append(0.05); p_away.append(0.95)
    probs = pd.DataFrame({"cook": p_cook, "away": p_away},
                         index=pd.DatetimeIndex(idx))
    return probs, pd.Series(y, index=probs.index)


def test_flags_confident_mislabels_only():
    probs, y = _pooled()
    flags = confident_flags(probs, y)
    assert len(flags) >= 1                       # found the planted errors
    assert all(f["given"] == "cook" and f["suggested"] == "away" for f in flags)
    # capped at 10% of 43 rows -> 5 max; every flag one of the planted rows
    assert len(flags) <= 5
    planted = {(T0 + timedelta(days=2, minutes=30 * i)).isoformat() for i in range(3)}
    assert {f["ts"] for f in flags} <= planted


def test_no_flags_when_labels_fit():
    probs, y = _pooled(n_bad=0)
    assert confident_flags(probs, y) == []


def test_tiny_classes_are_never_flagged():
    idx = pd.DatetimeIndex([T0 + timedelta(minutes=30 * i) for i in range(4)])
    probs = pd.DataFrame({"a": [0.1] * 4, "b": [0.9] * 4}, index=idx)
    y = pd.Series(["a"] * 4, index=idx)          # only 4 rows — below MIN_CLASS_COUNT
    assert confident_flags(probs, y) == []


def test_ledger_roundtrip_weights_and_reasks():
    repo = FakeRepo()
    probs, y = _pooled()
    flags = confident_flags(probs, y)
    prov = pd.Series("bootstrap", index=y.index, dtype=object)
    prov.iloc[-1] = "confirmed"                  # one human label among the bad rows
    update_suspects(repo, "alex", "root", flags, prov)

    # weights: flagged windows drop, clean windows stay at 1.0
    w = suspect_multipliers(repo, "alex", y.index)
    flagged_ts = [pd.Timestamp(f["ts"]) for f in flags]
    assert (w.loc[flagged_ts] == SUSPECT_WEIGHT).all()
    assert float(w.iloc[0]) == 1.0

    # re-asks: only the human-provenance suspect qualifies
    todo = suspects_to_ask(repo, "alex")
    assert all(t["provenance"] == "confirmed" for t in todo)
    if todo:
        mark_asked(repo, "alex", [t["ts"] for t in todo])
        assert suspects_to_ask(repo, "alex") == []   # never re-asked twice


def test_node_refresh_preserves_asked_and_prunes_stale():
    repo = FakeRepo()
    probs, y = _pooled()
    flags = confident_flags(probs, y)
    prov = pd.Series("confirmed", index=y.index, dtype=object)
    update_suspects(repo, "alex", "root", flags, prov)
    mark_asked(repo, "alex", [flags[0]["ts"]])
    # retrain flags the same window again -> asked flag must survive
    update_suspects(repo, "alex", "root", flags, prov)
    ledger = repo.settings["labels.suspects.alex"]
    assert ledger[flags[0]["ts"]]["asked"] is True
    # next retrain clears the node's flags -> stale entries drop out
    update_suspects(repo, "alex", "root", [], prov)
    assert repo.settings["labels.suspects.alex"] == {}

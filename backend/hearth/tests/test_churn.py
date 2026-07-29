"""Churn control: metrics, PCT weights, and the promotion veto."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from hearth.domain.training.churn import (CHURN_MAX, PCT_BOOST, churn_allows,
                                          churn_metrics, pct_weights)
from hearth.domain.training.evaluate import wilson_interval

T0 = datetime(2026, 7, 20, tzinfo=timezone.utc)


class FixedEst:
    """Estimator stub: predicts a fixed label sequence regardless of features."""
    def __init__(self, preds: list[str], classes=("a", "b")):
        self.preds, self.classes = preds, list(classes)
    def predict_proba(self, X) -> pd.DataFrame:
        rows = [{c: (0.9 if c == p else 0.1) for c in self.classes}
                for p in self.preds[:len(X)]]
        return pd.DataFrame(rows, index=X.index)


def _X(n):
    idx = pd.DatetimeIndex([T0 + timedelta(minutes=30 * i) for i in range(n)])
    return pd.DataFrame({"f": np.zeros(n)}, index=idx)


def test_churn_metrics_counts_flips():
    X = _X(4)
    y = pd.Series(["a", "a", "b", "b"], index=X.index)
    champ = FixedEst(["a", "a", "b", "a"])   # right, right, right, wrong
    new = FixedEst(["a", "b", "b", "b"])     # right, wrong, right, right
    m = churn_metrics(champ, new, X, y)
    assert m["n"] == 4
    assert m["churn"] == 0.5                 # rows 2 and 4 disagree
    assert m["nfr"] == 0.25                  # row 2: champ right, new wrong
    assert m["pfr"] == 0.25                  # row 4: champ wrong, new right


def test_pct_weights_boost_champion_correct_rows():
    X = _X(3)
    y = pd.Series(["a", "b", "a"], index=X.index)
    champ = FixedEst(["a", "a", "a"])        # right, wrong, right
    w = pct_weights(champ, X, y)
    assert list(w) == [PCT_BOOST, 1.0, PCT_BOOST]


def _metrics(acc, n, churn=None):
    m = {"accuracy_confirmed": acc, "n_confirmed": n}
    if churn is not None:
        m["churn"] = {"churn": churn, "nfr": 0.1, "pfr": 0.1, "n": 50}
    return m


def test_low_churn_always_allowed():
    ok, why = churn_allows(_metrics(0.8, 200, churn=0.05), _metrics(0.8, 200),
                           wilson_interval)
    assert ok and why == ""


def test_high_churn_without_decisive_gain_is_vetoed():
    ok, why = churn_allows(_metrics(0.80, 200, churn=CHURN_MAX + 0.1),
                           _metrics(0.80, 200), wilson_interval)
    assert not ok and "rewrite" in why


def test_high_churn_with_decisive_gain_is_allowed():
    ok, _ = churn_allows(_metrics(0.95, 200, churn=0.6),
                         _metrics(0.70, 200), wilson_interval)
    assert ok                                 # the rewrite is the point


def test_no_measurement_never_deadlocks():
    ok, _ = churn_allows(_metrics(0.8, 200), _metrics(0.8, 200), wilson_interval)
    assert ok                                 # no churn key -> no veto


def test_churny_without_comparable_labels_is_held():
    ok, why = churn_allows({"churn": {"churn": 0.5}}, {}, wilson_interval)
    assert not ok and "enough labels" in why

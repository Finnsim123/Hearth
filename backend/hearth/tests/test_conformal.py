"""Conformal sets: quantile validity, set semantics, and set-based abstention."""
from __future__ import annotations

import numpy as np
import pandas as pd

from hearth.domain.inference.conformal import (MIN_CAL, calibrate_from_pooled,
                                               conformal_quantile,
                                               prediction_set)
from hearth.domain.inference.output import (UNKNOWN, OutputPolicy,
                                            apply_abstain)


def _cal(n=200, seed=3):
    """Calibration rows: mostly separable two-class probs with a noisy tail."""
    rng = np.random.default_rng(seed)
    y, pa = [], []
    for _ in range(n):
        if rng.random() < 0.5:
            y.append("a"); pa.append(rng.uniform(0.6, 0.99))
        else:
            y.append("b"); pa.append(rng.uniform(0.01, 0.4))
    probs = pd.DataFrame({"a": pa, "b": [1 - p for p in pa]})
    return probs, pd.Series(y)


def test_quantile_gives_calibration_coverage():
    probs, y = _cal()
    conf = conformal_quantile(probs, y, alpha=0.10)
    assert conf is not None
    assert conf["coverage_cal"] >= 0.90            # the guarantee, on cal rows
    # LAC sets CAN be empty (that's the novelty signal), so with a sharp
    # classifier the average dips below 1 — it must just stay sane
    assert 0.5 <= conf["avg_set_size"] <= 2
    assert conf["n_cal"] == 200


def test_too_few_rows_returns_none():
    probs, y = _cal(n=MIN_CAL - 1)
    assert conformal_quantile(probs, y) is None


def test_basis_prefers_human_rows():
    probs, y = _cal(n=200)
    prov = pd.Series(["confirmed"] * 80 + ["bootstrap"] * 120)
    gold = pd.Series([False] * 200)
    conf = calibrate_from_pooled(probs, y, prov, gold)
    assert conf["basis"] == "human" and conf["n_cal"] == 80
    # too few human rows -> falls back to everything, honestly tagged
    prov2 = pd.Series(["confirmed"] * 10 + ["bootstrap"] * 190)
    conf2 = calibrate_from_pooled(probs, y, prov2, gold)
    assert conf2["basis"] == "all" and conf2["n_cal"] == 200


def test_set_semantics_commit_ambiguous_empty():
    conf = {"qhat": 0.55}                          # threshold p >= 0.45
    assert prediction_set({"cook": 0.8, "eat": 0.2}, conf) == ["cook"]
    both = prediction_set({"cook": 0.48, "eat": 0.47, "away": 0.05}, conf)
    assert both == ["cook", "eat"]                 # most-likely first
    assert prediction_set({"a": 0.3, "b": 0.3, "c": 0.4}, conf) == []
    assert prediction_set({"cook": 0.9}, None) is None   # no calibration


def test_abstain_on_empty_set_even_when_confident():
    pol = OutputPolicy(abstain_enabled=True, abstain_threshold=0.4)
    # weird window: model says 0.6 but NOTHING clears the conformal bar
    assert apply_abstain("cook", 0.6, pol, pred_set=[]) == UNKNOWN
    # singleton set + decent confidence -> committed
    assert apply_abstain("cook", 0.6, pol, pred_set=["cook"]) == "cook"
    # no calibration -> plain threshold behaviour, unchanged
    assert apply_abstain("cook", 0.6, pol, pred_set=None) == "cook"
    assert apply_abstain("cook", 0.3, pol, pred_set=None) == UNKNOWN
    # abstain disabled wins over everything
    off = OutputPolicy(abstain_enabled=False)
    assert apply_abstain("cook", 0.1, off, pred_set=[]) == "cook"

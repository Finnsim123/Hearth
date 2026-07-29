"""Class-balanced sample weights — the fix for the all-zero minority column."""
from __future__ import annotations

import numpy as np
import pandas as pd

from hearth.domain.training.trainer import class_balance_weights


def _y(home=795, sleeping=104, away=154):
    return pd.Series(["home"] * home + ["sleeping"] * sleeping + ["away"] * away)


def test_minority_class_gets_proportionally_more_weight():
    y = _y()
    w = class_balance_weights(y)
    w_home = w[0]
    w_sleep = w[795]
    w_away = w[795 + 104]
    assert w_sleep > w_away > w_home
    # ratio tracks inverse frequency: 795/104 ≈ 7.6x
    assert 6.0 < w_sleep / w_home < 8.5


def test_cap_limits_extreme_minorities():
    y = pd.Series(["home"] * 990 + ["rare"] * 10)     # raw balanced weight = 49.5x
    w = class_balance_weights(y, cap=8.0)
    # capped at 8 before normalization: ratio ~8/0.505 ≈ 16, nowhere near 49.5
    assert w[990] / w[0] < 20


def test_mean_normalized_and_balanced_input_is_neutral():
    y = _y()
    assert abs(class_balance_weights(y).mean() - 1.0) < 1e-9
    even = pd.Series(["a"] * 50 + ["b"] * 50)
    assert np.allclose(class_balance_weights(even), 1.0)

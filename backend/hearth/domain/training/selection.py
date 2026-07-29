"""Feature selection — measuring what actually helps, on data the model never saw.

The problem this module exists for: impurity (Gini) importance — the default the
trainer stored — is structurally biased toward continuous / high-cardinality
features (Strobl et al. 2007). A temperature sensor offers thousands of split
points, so it LOOKS important even when it carries no activity signal; the model
"keeps staring at the coffee machine's thermometer" and retraining never fixes
it, because the measurement itself is broken.

The fix (this file, stage 1): **held-out permutation importance** — shuffle one
column of the VALIDATION slice and measure how much held-out accuracy drops. A
feature the model doesn't truly need drops ~nothing; a harmful one can even go
negative. Unbiased w.r.t. cardinality because it scores predictions, not splits.

Later stages build on this measure: a noise gate (features that never beat
shuffling collect strikes across retrains — stability selection) and a
champion/challenger prune trial verified by the promotion gate.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MIN_VAL_ROWS = 20      # below this, permutation estimates are noise — fall back
N_REPEATS = 2          # shuffles per column (mean taken); 2 keeps a 600-col
                       # matrix in the tens of seconds on a small val slice


def holdout_permutation_importance(est, X_val: pd.DataFrame, y_val: pd.Series,
                                   *, n_repeats: int = N_REPEATS,
                                   seed: int = 42) -> dict[str, float]:
    """{column: mean drop in held-out accuracy when that column is shuffled}.

    Skips columns that are constant in the val slice (shuffling is a no-op —
    their importance is exactly 0, free). Returns {} when the val slice is too
    small to trust (< MIN_VAL_ROWS), so the caller can fall back to impurity."""
    if len(X_val) < MIN_VAL_ROWS or X_val.shape[1] == 0:
        return {}
    rng = np.random.default_rng(seed)

    def _acc(X) -> float:
        pred = est.predict_proba(X).idxmax(axis=1)
        return float((pred.to_numpy() == y_val.to_numpy()).mean())

    base = _acc(X_val)
    out: dict[str, float] = {}
    Xw = X_val.copy()
    for col in X_val.columns:
        vals = Xw[col].to_numpy()
        if len(np.unique(vals)) <= 1:          # constant in val → cannot matter
            out[col] = 0.0
            continue
        orig = vals.copy()
        drops = []
        for _ in range(n_repeats):
            Xw[col] = rng.permutation(orig)
            drops.append(base - _acc(Xw))
        Xw[col] = orig
        out[col] = float(np.mean(drops))
    return out

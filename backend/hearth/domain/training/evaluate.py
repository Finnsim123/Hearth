"""Evaluation — the honesty module.

Headline metric: accuracy on CONFIRMED labels only, with Wilson CI.
Also computed (and stored in the registry + hearth_ml.metrics):
accuracy_bootstrap (for context, clearly labeled), per-class P/R/F1, per-class
AUC + macro AUC, confusion matrix, calibration bins, global SHAP importances,
label-provenance counts. Drift: PSI per feature vs training distribution.
"""
from __future__ import annotations

import pandas as pd

from ..ports import Estimator


def evaluate_model(
    est: Estimator,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    provenance: pd.Series,
) -> dict:
    """Returns the metrics dict stored on ModelRecord (JSON-safe)."""
    raise NotImplementedError


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """CI for a proportion at tiny n — used by the promotion gate."""
    raise NotImplementedError


def population_stability_index(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    """Feature drift score; surfaced per-feature in the Models drift panel."""
    raise NotImplementedError

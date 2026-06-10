"""Estimator implementations (Estimator port, ADR-9).

v1: RandomForest (class_weight='balanced', min_samples_leaf=5 — settings that
survived contact with reality in the prototype) + TreeSHAP explanations.
Future: calibrated GBM; HEPA-embedding + small head (see RESEARCH.md §4) —
all behind the same Protocol, compared in the registry UI.
"""
from __future__ import annotations

import pandas as pd


class RandomForestEstimator:
    """Implements domain.ports.Estimator."""

    def __init__(self, n_estimators: int = 300, min_samples_leaf: int = 5):
        raise NotImplementedError

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        raise NotImplementedError

    def predict_proba(self, X: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def explain(self, X: pd.DataFrame) -> pd.DataFrame:
        """TreeSHAP per-row attributions; guards against shap API changes by
        normalizing output shape here (lesson: silent except-pass hid breakage)."""
        raise NotImplementedError

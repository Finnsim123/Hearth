"""Estimator implementations (Estimator port, ADR-9).

RandomForest with settings that survived contact with reality in the
prototype. explain() normalizes SHAP's shifting output shapes (list vs 3-D
array across versions) instead of silently swallowing errors.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

log = logging.getLogger(__name__)


DEFAULT_PARAMS: dict = {"n_estimators": 300, "min_samples_leaf": 5}

# Small, sane search space — RFs don't reward exotic grids on small data.
PARAM_DISTRIBUTIONS: dict = {
    "n_estimators": [200, 300, 500],
    "min_samples_leaf": [2, 3, 5, 8],
    "max_features": ["sqrt", 0.3, 0.5],
    "max_depth": [None, 12, 20],
}


def tune_hyperparams(X, y, n_iter: int = 15, cv_splits: int = 3,
                     distributions: dict | None = None) -> dict:
    """Randomized search with TIME-SERIES CV (rolling origin, never shuffled —
    shuffled CV leaks adjacent windows and inflates scores). Returns best
    params. Scoring f1_macro: balanced view across rare classes."""
    from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit

    search = RandomizedSearchCV(
        RandomForestClassifier(class_weight="balanced", random_state=42, n_jobs=-1),
        distributions or PARAM_DISTRIBUTIONS, n_iter=n_iter,
        cv=TimeSeriesSplit(n_splits=cv_splits), scoring="f1_macro",
        random_state=42, n_jobs=-1)
    search.fit(X, y)
    log.info("tuned hyperparams: %s (f1_macro=%.3f)", search.best_params_, search.best_score_)
    return dict(search.best_params_)


class RandomForestEstimator:
    """Implements domain.ports.Estimator."""

    supports_sample_weight = True

    def __init__(self, **params):
        self.params = {**DEFAULT_PARAMS, **params}
        self.model = RandomForestClassifier(
            **self.params, class_weight="balanced", random_state=42, n_jobs=-1)
        self.columns: list[str] = []

    @property
    def classes_(self) -> list[str]:
        return list(self.model.classes_)

    def importances(self) -> dict[str, float]:
        imp = getattr(self.model, "feature_importances_", None)
        if imp is None or not self.columns:
            return {}
        return {c: float(v) for c, v in zip(self.columns, imp)}

    def _align(self, X: pd.DataFrame) -> pd.DataFrame:
        """Inference rows may miss/add columns vs training — align hard."""
        out = X.copy()
        for c in self.columns:
            if c not in out.columns:
                out[c] = 0.0
        return out[self.columns]

    def fit(self, X: pd.DataFrame, y: pd.Series, sample_weight=None) -> None:
        self.columns = list(X.columns)
        self.model.fit(X, y, sample_weight=sample_weight)

    def predict_proba(self, X: pd.DataFrame) -> pd.DataFrame:
        probs = self.model.predict_proba(self._align(X))
        df = pd.DataFrame(probs, index=X.index, columns=self.classes_)
        if getattr(self, "calibrators", None):
            for cls, iso in self.calibrators.items():
                if cls in df.columns:
                    df[cls] = iso.predict(df[cls].to_numpy())
            row_sums = df.sum(axis=1).replace(0, 1.0)
            df = df.div(row_sums, axis=0)
        return df

    def calibrate(self, X_val: pd.DataFrame, y_val: pd.Series) -> bool:
        """Per-class isotonic regression on a held-out split — forests are
        systematically mis-calibrated and every downstream threshold (asking,
        evidence cap, promotion) reads confidence as a probability. Returns True
        if any class was calibrated."""
        from sklearn.isotonic import IsotonicRegression
        raw = self.model.predict_proba(self._align(X_val))
        raw = pd.DataFrame(raw, index=X_val.index, columns=self.classes_)
        self.calibrators = {}
        for cls in self.classes_:
            target = (y_val == cls).astype(float)
            if target.nunique() < 2:
                continue
            iso = IsotonicRegression(y_min=0.001, y_max=0.999,
                                     out_of_bounds="clip")
            iso.fit(raw[cls].to_numpy(), target.to_numpy())
            self.calibrators[cls] = iso
        return bool(self.calibrators)

    def explain(self, X: pd.DataFrame) -> pd.DataFrame:
        """Per-row SHAP for the predicted class; empty df if shap unavailable."""
        try:
            import shap
        except ImportError:
            return pd.DataFrame(index=X.index)
        try:
            Xa = self._align(X)
            values = shap.TreeExplainer(self.model).shap_values(Xa)
            preds = self.model.predict(Xa)
            cls_idx = {c: i for i, c in enumerate(self.classes_)}
            rows = []
            for i, pred in enumerate(preds):
                k = cls_idx[pred]
                if isinstance(values, list):              # old API: list per class
                    row = values[k][i]
                else:                                     # new API: (n, feat, cls)
                    row = np.asarray(values)[i, :, k]
                rows.append(row)
            return pd.DataFrame(rows, index=X.index, columns=self.columns)
        except Exception as exc:
            log.warning("SHAP explanation failed: %s", exc)
            return pd.DataFrame(index=X.index)

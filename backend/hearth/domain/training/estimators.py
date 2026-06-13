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


GBT_DEFAULT_PARAMS: dict = {"n_estimators": 200, "learning_rate": 0.05, "max_depth": 3}


def _tree_shap(est, X: pd.DataFrame) -> pd.DataFrame:
    """Per-row SHAP for the predicted class of a TREE model; empty df if shap is
    unavailable. Shared by the forest and gradient-boosted estimators."""
    try:
        import shap
    except ImportError:
        return pd.DataFrame(index=X.index)
    try:
        Xa = est._align(X)
        values = shap.TreeExplainer(est.model).shap_values(Xa)
        preds = est.model.predict(Xa)
        cls_idx = {c: i for i, c in enumerate(est.classes_)}
        arr = None if isinstance(values, list) else np.asarray(values)
        rows = []
        for i, pred in enumerate(preds):
            k = cls_idx[pred]
            if isinstance(values, list):                  # old API: list per class
                row = values[k][i]
            elif arr.ndim == 3:                           # multiclass: (n, feat, cls)
                row = arr[i, :, k]
            else:                                         # binary: (n, feat) = +class
                row = arr[i] if k == 1 else -arr[i]
            rows.append(row)
        return pd.DataFrame(rows, index=X.index, columns=est.columns)
    except Exception as exc:
        log.warning("SHAP explanation failed: %s", exc)
        return pd.DataFrame(index=X.index)


class _SklearnEstimator:
    """Shared Estimator-port behaviour over any sklearn classifier with
    predict_proba (align, isotonic calibration, importances). Subclasses set the
    model and may override importances()/explain()."""

    supports_sample_weight = True

    def __init__(self, model):
        self.model = model
        self.columns: list[str] = []
        self.calibrators: dict = {}

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
        if self.calibrators:
            for cls, iso in self.calibrators.items():
                if cls in df.columns:
                    df[cls] = iso.predict(df[cls].to_numpy())
            row_sums = df.sum(axis=1).replace(0, 1.0)
            df = df.div(row_sums, axis=0)
        return df

    def calibrate(self, X_val: pd.DataFrame, y_val: pd.Series) -> bool:
        """Per-class isotonic regression on a held-out split, so every downstream
        threshold (asking, evidence cap, promotion) reads confidence as a real
        probability. Returns True if any class was calibrated."""
        from sklearn.isotonic import IsotonicRegression
        raw = self.model.predict_proba(self._align(X_val))
        raw = pd.DataFrame(raw, index=X_val.index, columns=self.classes_)
        self.calibrators = {}
        for cls in self.classes_:
            target = (y_val == cls).astype(float)
            if target.nunique() < 2:
                continue
            iso = IsotonicRegression(y_min=0.001, y_max=0.999, out_of_bounds="clip")
            iso.fit(raw[cls].to_numpy(), target.to_numpy())
            self.calibrators[cls] = iso
        return bool(self.calibrators)

    def explain(self, X: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(index=X.index)        # no attribution by default


class RandomForestEstimator(_SklearnEstimator):
    """Implements domain.ports.Estimator. The known-good tabular default."""

    def __init__(self, **params):
        self.params = {**DEFAULT_PARAMS, **params}
        super().__init__(RandomForestClassifier(
            **self.params, class_weight="balanced", random_state=42, n_jobs=-1))

    def explain(self, X: pd.DataFrame) -> pd.DataFrame:
        return _tree_shap(self, X)


class GradientBoostedEstimator(_SklearnEstimator):
    """Gradient-boosted trees — usually the strongest tabular learner once a home
    has accumulated labels (model_levers.md G1). Imbalance handled via
    sample_weight (no class_weight on this sklearn estimator)."""

    def __init__(self, **params):
        from sklearn.ensemble import GradientBoostingClassifier
        self.params = {**GBT_DEFAULT_PARAMS, **params}
        super().__init__(GradientBoostingClassifier(**self.params, random_state=42))

    def explain(self, X: pd.DataFrame) -> pd.DataFrame:
        return _tree_shap(self, X)


class LogisticEstimator(_SklearnEstimator):
    """Logistic regression — the honest linear baseline (model_levers.md G1/G3).
    Importance = mean |coefficient| across classes; SHAP not applicable."""

    def __init__(self, **params):
        from sklearn.linear_model import LogisticRegression
        self.params = {"max_iter": 1000, "class_weight": "balanced", **params}
        super().__init__(LogisticRegression(**self.params))

    def importances(self) -> dict[str, float]:
        coef = getattr(self.model, "coef_", None)
        if coef is None or not self.columns:
            return {}
        mag = np.abs(np.asarray(coef, dtype=float))
        mag = mag.mean(axis=0) if mag.ndim == 2 else mag
        return {c: float(v) for c, v in zip(self.columns, mag)}


class IdentityEmbedder:
    """Default Embedder (domain.ports.Embedder): passthrough. Replaced by a
    trained self-supervised encoder — adapters/hepa_embedder.py, the JEPA /
    world-model bet (RESEARCH.md §World models) — once one is installed."""

    def embed(self, X: pd.DataFrame) -> pd.DataFrame:
        return X


class EmbeddingEstimator:
    """Classify in a learned EMBEDDING space (LeCun world-model / JEPA direction;
    RESEARCH.md §4 + §World models). Composes an Embedder (a self-supervised
    encoder, or identity until one exists) with a cheap downstream head, and
    implements the Estimator port so it slots into make_estimator/the family
    selector as 'embedding'. With the identity embedder it equals its head on raw
    features; the value arrives when a real encoder is plugged in behind the
    Embedder port and few-label heads learn from its representations."""

    def __init__(self, embedder=None, head: str = "random_forest", **head_params):
        self.embedder = embedder or IdentityEmbedder()
        self.head = make_estimator(head, **head_params)
        self.supports_sample_weight = self.head.supports_sample_weight

    def _embed(self, X: pd.DataFrame) -> pd.DataFrame:
        try:
            out = self.embedder.embed(X)
            return out if out is not None and len(out) == len(X) else X
        except Exception:
            log.warning("embedder failed — falling back to raw features")
            return X

    def fit(self, X, y, sample_weight=None) -> None:
        self.head.fit(self._embed(X), y, sample_weight=sample_weight)

    def predict_proba(self, X):
        return self.head.predict_proba(self._embed(X))

    def calibrate(self, X_val, y_val) -> bool:
        return self.head.calibrate(self._embed(X_val), y_val)

    def importances(self) -> dict:
        return self.head.importances()

    def explain(self, X):
        return self.head.explain(self._embed(X))

    @property
    def classes_(self) -> list[str]:
        return self.head.classes_


_FAMILIES = {
    "random_forest": RandomForestEstimator,
    "gradient_boosting": GradientBoostedEstimator,
    "logistic": LogisticEstimator,
    "embedding": EmbeddingEstimator,
}
# friendly aliases accepted from settings
_FAMILY_ALIASES = {"rf": "random_forest", "gbt": "gradient_boosting",
                   "gbm": "gradient_boosting", "gradient_boosted": "gradient_boosting",
                   "logreg": "logistic", "logistic_regression": "logistic",
                   "jepa": "embedding", "hepa": "embedding"}

KNOWN_FAMILIES = tuple(_FAMILIES)


def make_estimator(family: str | None = "random_forest", **params):
    """Build an Estimator for the chosen model family (defaults to / falls back
    on random_forest for an unknown family)."""
    key = (family or "random_forest").lower()
    key = _FAMILY_ALIASES.get(key, key)
    return _FAMILIES.get(key, RandomForestEstimator)(**params)

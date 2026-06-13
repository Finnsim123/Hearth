"""Estimator families + factory (model_levers.md G1; gap analysis G2/G4)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hearth.domain.training.estimators import (
    EmbeddingEstimator, GradientBoostedEstimator, KNOWN_FAMILIES,
    LogisticEstimator, RandomForestEstimator, make_estimator,
)


@pytest.fixture
def xy():
    idx = pd.date_range("2026-06-01", periods=120, freq="30min", tz="UTC")
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"signal": np.r_[np.zeros(60), np.ones(60)],
                      "noise": rng.normal(size=120)}, index=idx)
    y = pd.Series(["home"] * 60 + ["movie"] * 60, index=idx)
    return X, y


def test_make_estimator_dispatch():
    assert isinstance(make_estimator("random_forest"), RandomForestEstimator)
    assert isinstance(make_estimator("gradient_boosting"), GradientBoostedEstimator)
    assert isinstance(make_estimator("gbt"), GradientBoostedEstimator)        # alias
    assert isinstance(make_estimator("logistic"), LogisticEstimator)
    assert isinstance(make_estimator("embedding"), EmbeddingEstimator)
    assert isinstance(make_estimator("jepa"), EmbeddingEstimator)             # alias
    assert isinstance(make_estimator("nonsense"), RandomForestEstimator)      # fallback
    assert set(KNOWN_FAMILIES) == {"random_forest", "gradient_boosting", "logistic", "embedding"}


def test_embedding_estimator_identity_passthrough(xy):
    """With the default identity embedder, the embedding family equals its head
    on raw features — a real seam that's selectable/testable now (JEPA bet)."""
    X, y = xy
    est = make_estimator("embedding")          # identity embedder + RF head
    est.fit(X, y)
    assert est.supports_sample_weight is True
    imp = est.importances()
    assert imp and imp["signal"] >= imp["noise"]   # head sees the raw features
    assert est.predict_proba(X.tail(1)).idxmax(axis=1).iloc[0] in ("home", "movie")


@pytest.mark.parametrize("family", ["random_forest", "gradient_boosting", "logistic", "embedding"])
def test_family_implements_port(xy, family):
    X, y = xy
    est = make_estimator(family)
    assert est.supports_sample_weight is True
    est.fit(X, y)
    probs = est.predict_proba(X)
    assert list(probs.columns) == est.classes_
    assert np.allclose(probs.sum(axis=1).to_numpy(), 1.0, atol=1e-6)
    # the signal feature dominates importance for every family
    imp = est.importances()
    assert imp and imp["signal"] >= imp["noise"]
    assert est.calibrate(X, y) is True            # both classes present
    # predictions still normalise after calibration
    assert np.allclose(est.predict_proba(X).sum(axis=1).to_numpy(), 1.0, atol=1e-6)


def test_predicts_the_learnable_class(xy):
    X, y = xy
    for family in ("gradient_boosting", "logistic"):
        est = make_estimator(family)
        est.fit(X, y)
        # signal=1 region -> movie
        pred = est.predict_proba(X.tail(1)).idxmax(axis=1).iloc[0]
        assert pred == "movie"

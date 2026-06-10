"""Evaluation — the honesty module.

Headline metric: accuracy on CONFIRMED labels only, with a Wilson interval.
Bootstrap-agreement accuracy is reported separately and clearly named — the
prototype's '90%' conflated the two (RESEARCH.md lesson #3).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, roc_auc_score

from ..ports import Estimator
from ..schemas import Provenance


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """CI for a proportion at tiny n — drives the promotion gate."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def population_stability_index(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    """Feature drift score (>0.2 = investigate). Quantile bins on `expected`."""
    if expected.empty or actual.empty:
        return 0.0
    qs = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(qs) < 3:
        return 0.0
    e = np.clip(np.histogram(expected, qs)[0] / len(expected), 1e-6, None)
    a = np.clip(np.histogram(actual, qs)[0] / len(actual), 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def evaluate_model(
    est: Estimator,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    provenance: pd.Series,
) -> dict:
    """Metrics dict for the registry (JSON-safe). Empty val -> {}."""
    if X_val.empty:
        return {}
    probs = est.predict_proba(X_val)
    y_pred = probs.idxmax(axis=1)
    classes = sorted(y_val.unique())

    out: dict = {"n_val": int(len(y_val))}
    correct = (y_pred == y_val)

    conf_mask = provenance == Provenance.CONFIRMED.value
    n_conf = int(conf_mask.sum())
    out["n_confirmed"] = n_conf
    if n_conf:
        k = int(correct[conf_mask].sum())
        lo, hi = wilson_interval(k, n_conf)
        out["accuracy_confirmed"] = round(k / n_conf, 4)
        out["accuracy_confirmed_ci"] = [round(lo, 4), round(hi, 4)]
    out["accuracy_bootstrap"] = round(float(correct[~conf_mask].mean()), 4) if (~conf_mask).any() else None

    prec, rec, f1, support = precision_recall_fscore_support(
        y_val, y_pred, labels=classes, zero_division=0)
    out["per_class"] = {c: {"precision": round(float(p), 4), "recall": round(float(r), 4),
                            "f1": round(float(f), 4), "support": int(s)}
                        for c, p, r, f, s in zip(classes, prec, rec, f1, support)}
    try:
        present = [c for c in est.classes_ if c in classes]
        if len(present) > 1:
            out["auc_macro"] = round(float(roc_auc_score(
                y_val, probs[present], multi_class="ovr",
                average="macro", labels=present)), 4)
    except Exception:
        pass
    cm = confusion_matrix(y_val, y_pred, labels=classes)
    out["confusion"] = {"labels": classes, "matrix": cm.tolist()}
    return out

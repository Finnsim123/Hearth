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


def reliability_metrics(probs: pd.DataFrame, y_true: pd.Series, bins: int = 10) -> dict:
    """Whether calibrated confidence is honest (audit F4): multiclass Brier score
    and Expected Calibration Error on a HELD-OUT slice. Brier ↓ = sharper+truer;
    ECE = mean |confidence − accuracy| across confidence bins (0 = '0.75 really
    means 75%'). Computed on a slice NOT used to fit the calibrators."""
    classes = list(probs.columns)
    p = probs[classes].to_numpy()
    onehot = pd.get_dummies(y_true).reindex(columns=classes, fill_value=0).to_numpy()
    brier = float(((p - onehot) ** 2).sum(axis=1).mean())
    conf = probs.max(axis=1).to_numpy()
    acc = (probs.idxmax(axis=1).to_numpy() == y_true.to_numpy()).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    n = len(conf)
    ece = 0.0
    reliability = []      # per-bin points for the reliability diagram (UX2)
    for i in range(bins):
        m = (conf > edges[i]) & (conf <= edges[i + 1])
        if m.sum():
            ece += (m.sum() / n) * abs(acc[m].mean() - conf[m].mean())
            reliability.append({"conf": round(float(conf[m].mean()), 4),
                                "acc": round(float(acc[m].mean()), 4),
                                "n": int(m.sum())})
    return {"brier": round(brier, 4), "ece": round(float(ece), 4),
            "n_check": int(n), "reliability": reliability}


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
    gold: pd.Series | None = None,
) -> dict:
    """Metrics dict for the registry (JSON-safe). Empty val -> {}.

    `gold` (audit F1): confirmed windows that were asked at RANDOM (ε-explore),
    so an unbiased sample of the home's life. `accuracy_confirmed` pools those
    with uncertainty-sampled hard cases and so understates true performance;
    `accuracy_gold` is the honest headline and what the promotion gate prefers.
    """
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

    # unbiased headline: confirmed AND gold (random ε-explore query)
    gold_mask = (conf_mask & gold.reindex(y_val.index, fill_value=False)) \
        if gold is not None else pd.Series(False, index=y_val.index)
    n_gold = int(gold_mask.sum())
    out["n_gold"] = n_gold
    if n_gold:
        kg = int(correct[gold_mask].sum())
        glo, ghi = wilson_interval(kg, n_gold)
        out["accuracy_gold"] = round(kg / n_gold, 4)
        out["accuracy_gold_ci"] = [round(glo, 4), round(ghi, 4)]
    out["accuracy_bootstrap"] = round(float(correct[~conf_mask].mean()), 4) if (~conf_mask).any() else None

    prec, rec, f1, support = precision_recall_fscore_support(
        y_val, y_pred, labels=classes, zero_division=0)
    out["per_class"] = {c: {"precision": round(float(p), 4), "recall": round(float(r), 4),
                            "f1": round(float(f), 4), "support": int(s)}
                        for c, p, r, f, s in zip(classes, prec, rec, f1, support)}
    try:
        present = [c for c in est.classes_ if c in classes]
        if len(present) == 2:
            # binary: roc_auc_score wants a 1-D score for the positive class,
            # not the (n, 2) probs + multi_class path (which raises on 2 classes).
            pos = present[1]
            out["auc_macro"] = round(float(roc_auc_score(
                (y_val == pos).astype(int), probs[pos])), 4)
        elif len(present) > 2:
            out["auc_macro"] = round(float(roc_auc_score(
                y_val, probs[present], multi_class="ovr",
                average="macro", labels=present)), 4)
    except Exception:
        pass
    cm = confusion_matrix(y_val, y_pred, labels=classes)
    out["confusion"] = {"labels": classes, "matrix": cm.tolist()}
    return out

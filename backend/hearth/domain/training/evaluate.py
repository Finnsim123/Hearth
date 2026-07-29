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


# ── blocked rolling-origin CV (Bergmeir & Benítez) ───────────────────────────
# A single 7-day holdout is a tiny, high-variance sample — and every automated
# decision (promotion gate, cadence, strikes, prune trials) keys off it. Blocked
# rolling-origin folds use MORE of the data as out-of-sample evidence while
# respecting time order: fold i trains on everything before its val block and
# predicts the block after. Decision metrics pool the out-of-sample predictions
# across folds (k/n stays meaningful for Wilson bounds); the SHIPPED model and
# its calibration still come from the most recent fold only.
from datetime import timedelta as _timedelta


def rolling_origin_folds(index, end, val_days: int, n_folds: int = 3,
                         min_train: int = 10, min_val: int = 5) -> list:
    """[(train_mask, val_mask), …] oldest fold first; the LAST fold is today's
    split (val = the most recent val_days). Folds that would leave too little
    train or val data are dropped — a young install naturally gets 1 fold."""
    folds = []
    for i in range(n_folds, 0, -1):
        val_end = end - _timedelta(days=(i - 1) * val_days)
        val_start = val_end - _timedelta(days=val_days)
        tm = index < val_start
        vm = (index >= val_start) & (index < val_end)
        if tm.sum() >= min_train and vm.sum() >= min_val:
            folds.append((tm, vm))
    return folds


class PrefitProba:
    """Adapter: pooled out-of-sample probabilities behaving like an estimator,
    so evaluate_model can score CV-pooled predictions unchanged."""

    def __init__(self, probs: pd.DataFrame) -> None:
        self._p = probs
        self.classes_ = list(probs.columns)

    def predict_proba(self, X) -> pd.DataFrame:
        return self._p.loc[X.index]


def pooled_oos_predictions(fit_fn, feats, labels, provenance, gold, folds):
    """Fit per fold (fit_fn(train_mask) → estimator), predict each fold's val
    block, pool. Returns (probs, y, prov, gold, final_est) where final_est is
    the LAST (most recent) fold's estimator — the one that ships. None when no
    fold produced scoreable rows. Val rows with classes unseen in that fold's
    train are dropped (can't be predicted, mustn't count against the model)."""
    frames, ys, ps, gs = [], [], [], []
    final_est = None
    for tm, vm in folds:
        est = fit_fn(tm)
        final_est = est
        yv = labels[vm]
        keep = yv.isin(set(labels[tm].unique()))
        Xv, yv = feats[vm][keep], yv[keep]
        if Xv.empty:
            continue
        frames.append(est.predict_proba(Xv))
        ys.append(yv)
        ps.append(provenance[vm][keep])
        gs.append(gold[vm][keep])
    if not frames or final_est is None:
        return None
    cols = sorted(set().union(*[set(f.columns) for f in frames]))
    probs = pd.concat([f.reindex(columns=cols, fill_value=0.0) for f in frames])
    return probs, pd.concat(ys), pd.concat(ps), pd.concat(gs), final_est


def evaluate_model(
    est: Estimator,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    provenance: pd.Series,
    gold: pd.Series | None = None,
    tz: str = "UTC",
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

    # coverage/precision curve (UX6): at each confidence threshold, what fraction
    # of windows the model commits on (coverage) and how accurate those are
    # (precision). Drives the live preview under the abstain slider — the
    # "set-with-preview" pattern. Indicative (pre-calibration eval probs).
    conf = probs.max(axis=1).to_numpy()
    corr = correct.to_numpy()
    n = len(conf)
    curve = []
    for t in np.linspace(0.0, 0.9, 10):
        m = conf >= t
        cov = int(m.sum())
        curve.append({"t": round(float(t), 2),
                      "coverage": round(cov / n, 4) if n else 0.0,
                      "precision": round(float(corr[m].mean()), 4) if cov else None})
    out["coverage_curve"] = curve

    # slice analysis (UX5): accuracy by daypart × activity surfaces failure
    # pockets that the aggregate hides (great at 8pm, poor at 3am). Free here —
    # the windows are already time-stamped (SliceFinder idiom).
    try:
        from zoneinfo import ZoneInfo

        from ..features.pipeline import _bucket
        try:
            hours = y_val.index.tz_convert(ZoneInfo(tz)).hour
        except Exception:
            hours = y_val.index.hour
        dp = np.array([int(_bucket(int(h))) for h in hours])
        corr = correct.to_numpy()
        yv = y_val.to_numpy()
        rows = []
        for cls in classes:
            cells = []
            for b in range(4):
                mask = (yv == cls) & (dp == b)
                n = int(mask.sum())
                cells.append({"acc": round(float(corr[mask].mean()), 4) if n else None,
                              "n": n})
            rows.append({"activity": cls, "cells": cells})
        out["slices"] = {"dayparts": ["night", "morning", "afternoon", "evening"],
                         "by_activity_daypart": rows}
    except Exception:
        pass
    return out

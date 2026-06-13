"""Model-to-LLM feedback (OCTree + ZARA, llm_layer_design §f).

Turns a trained model's honest artifacts (confusion matrix, importances, evidence
profile) plus a ZARA-style pairwise discriminative-statistics analysis into a
compact summary the feature architect can reason over to propose SEPARATING
features for the classes the model confuses. Pure functions; no LLM, no I/O.

Stopping criteria live here too; the "did it improve?" stop reuses the existing
promotion_gate (a revision is kept only if the retrained model clears the gate).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

D_CAP = 10.0  # cap Cohen's d (perfect separation -> finite, comparable number)


def top_confusions(confusion: dict | None, k: int = 3, min_count: int = 5) -> list[tuple]:
    """Largest off-diagonal cells of the confusion matrix as (true, pred, count),
    descending, keeping only those at or above `min_count`."""
    confusion = confusion or {}
    labels = confusion.get("labels") or []
    matrix = confusion.get("matrix") or []
    pairs = []
    for i, row in enumerate(matrix):
        for j, count in enumerate(row):
            if i != j and i < len(labels) and j < len(labels) and count >= min_count:
                pairs.append((labels[i], labels[j], int(count)))
    pairs.sort(key=lambda t: -t[2])
    return pairs[:k]


def cohens_d(a, b) -> float:
    """|Cohen's d| between two numeric samples, capped at D_CAP. Zero pooled
    variance with different means -> D_CAP (a perfectly separating feature)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    na, nb = len(a), len(b)
    pooled = (((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / max(na + nb - 2, 1)) ** 0.5
    if pooled == 0:
        return 0.0 if a.mean() == b.mean() else D_CAP
    return min(abs(a.mean() - b.mean()) / pooled, D_CAP)


def discriminative_stats(X: pd.DataFrame, y: pd.Series, pairs: list[tuple],
                         top_n: int = 5) -> dict:
    """For each confused (true, pred) pair, the features that best separate the
    two classes by |Cohen's d| — the verifiable linguistic prior ZARA feeds the
    LLM. {"<true>_vs_<pred>": [{"feature","cohens_d"}, ...]}."""
    out: dict = {}
    for true_lab, pred_lab, _ in pairs:
        a, b = X[y == true_lab], X[y == pred_lab]
        if len(a) == 0 or len(b) == 0:
            continue
        scored = [(col, cohens_d(a[col].to_numpy(), b[col].to_numpy())) for col in X.columns]
        scored.sort(key=lambda t: -t[1])
        out[f"{true_lab}_vs_{pred_lab}"] = [
            {"feature": c, "cohens_d": round(float(d), 3)} for c, d in scored[:top_n]]
    return out


def build_feedback(metrics: dict, X: pd.DataFrame, y: pd.Series, *,
                   confusion_k: int = 3) -> dict:
    """Assemble the compact feedback summary sent to the architect. Reads the
    model's metrics (already computed by the trainer) and the feature matrix."""
    confusion = metrics.get("confusion") or {}
    pairs = top_confusions(confusion, k=confusion_k)
    importances = metrics.get("feature_importances") or {}
    importance_all = metrics.get("importance_all") or {}
    zero = [c for c in X.columns if c not in importance_all][:10]
    return {
        "validation": {
            "accuracy_confirmed": metrics.get("accuracy_confirmed"),
            "accuracy_confirmed_ci": metrics.get("accuracy_confirmed_ci"),
            "n_confirmed": metrics.get("n_confirmed", 0),
            "auc_macro": metrics.get("auc_macro"),
        },
        "per_class": metrics.get("per_class") or {},
        "confusion_top_pairs": [{"true": t, "pred": p, "count": c} for t, p, c in pairs],
        "feature_importance_top": dict(sorted(importances.items(),
                                              key=lambda kv: -kv[1])[:10]),
        "feature_importance_zero": zero,
        "evidence_profile": metrics.get("evidence_profile") or {},
        "discriminative_stats": discriminative_stats(X, y, pairs),
    }


# ── stopping criteria (llm_layer_design §f) ──────────────────────────────────
def feedback_should_run(n_confirmed: int, min_confirmed: int = 30) -> bool:
    """Don't optimise against too few confirmed labels — below the threshold the
    loop refuses to run (also prevents the cold-start circularity: it never
    optimises against bootstrap-only signal)."""
    return n_confirmed >= min_confirmed


def confusion_unresolved(confusion: dict | None, floor: int = 5) -> bool:
    """True while there is still a clear pair to target (largest off-diagonal
    cell >= floor). When false, there's nothing worth a revision round."""
    return bool(top_confusions(confusion, k=1, min_count=floor))

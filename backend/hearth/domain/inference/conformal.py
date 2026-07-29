"""Conformal prediction sets — calibrated "it's one of these" with a guarantee.

A single softmax number ("cooking, 61%") hides the difference between three
situations that should be handled differently: clearly cooking; genuinely torn
between cooking and eating; and looks like NOTHING seen before. Split conformal
prediction (Vovk et al.; Angelopoulos & Bates 2021 tutorial) separates them
with a distribution-free coverage guarantee: calibrate a score threshold on
held-out rows so that, at level 1−α, the SET {classes with p ≥ 1−q̂} contains
the true label at least (1−α) of the time — no matter how miscalibrated the
underlying classifier is. Set of 1 = commit. Set of 2+ = honest ambiguity
(and a great question for the asking policy). EMPTY set = this window doesn't
credibly look like ANY known activity at the calibrated level — the novelty
signal, and the principled trigger for publishing "unknown" instead of a
confident wrong guess.

Hearth calibrates per hierarchy node on the pooled blocked-CV out-of-sample
predictions the trainer already produces (every row scored by a fold that
never trained on it — the exchangeability conformal needs). Human-provenance
rows are preferred as calibration when there are enough: bootstrap labels are
noisy, and noisy calibration labels quietly void the coverage guarantee.
The score is the simplest valid one (1 − p_true, "LAC"): with 5–10 activity
classes the fancier APS/RAPS variants buy little and cost explainability.
"""
from __future__ import annotations

import logging
from math import ceil

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

ALPHA = 0.10          # target: the set misses the true label ≤ 10% of the time
MIN_CAL = 30          # below this many calibration rows the quantile is junk
MIN_CAL_HUMAN = 50    # prefer human-label calibration from this count up


def conformal_quantile(probs: pd.DataFrame, y: pd.Series,
                       alpha: float = ALPHA) -> dict | None:
    """Split-conformal threshold from calibration rows (probs: class columns;
    y: true labels, positionally aligned). Returns {qhat, alpha, n_cal,
    avg_set_size, coverage_cal} or None when there's too little to calibrate.
    Finite-sample corrected: quantile level ceil((n+1)(1−α))/n, 'higher'
    interpolation — the direction that keeps the guarantee."""
    mask = y.isin(probs.columns).to_numpy()
    if int(mask.sum()) < MIN_CAL:
        return None
    p = probs.loc[mask]
    yv = y[mask]
    p_true = np.array([row[lab] for row, (_, lab)
                       in zip(p.to_dict("records"), yv.items())])
    scores = 1.0 - p_true
    n = len(scores)
    level = min(1.0, ceil((n + 1) * (1 - alpha)) / n)
    qhat = float(np.quantile(scores, level, method="higher"))
    thr = 1.0 - qhat
    sizes = (p.to_numpy() >= thr).sum(axis=1)
    return {"qhat": round(qhat, 4), "alpha": alpha, "n_cal": int(n),
            "avg_set_size": round(float(sizes.mean()), 2),
            "coverage_cal": round(float((p_true >= thr).mean()), 4)}


def calibrate_from_pooled(probs_p: pd.DataFrame, y_p: pd.Series,
                          prov_p: pd.Series, gold_p: pd.Series) -> dict | None:
    """Trainer entrypoint: pick the calibration basis, then calibrate.
    Human rows (confirmed/corrected/gold) when there are MIN_CAL_HUMAN+ of
    them — their labels are trustworthy, so the guarantee is real. Otherwise
    all pooled rows, honestly tagged basis="all": coverage is then w.r.t. the
    (partly rule-generated) label mix, useful but weaker."""
    try:
        human = (prov_p.isin(("confirmed", "corrected")).to_numpy()
                 | gold_p.astype(bool).to_numpy())
        if int(human.sum()) >= MIN_CAL_HUMAN:
            out = conformal_quantile(probs_p.loc[human], y_p[human])
            if out:
                out["basis"] = "human"
                return out
        out = conformal_quantile(probs_p, y_p)
        if out:
            out["basis"] = "all"
        return out
    except Exception:
        log.debug("conformal calibration failed", exc_info=True)
        return None


def prediction_set(probabilities: dict, conf: dict | None) -> list[str] | None:
    """The set for one window, most-likely first. None = no calibration
    available (young install, rules fallback) — callers change nothing."""
    if not conf or "qhat" not in conf:
        return None
    thr = 1.0 - float(conf["qhat"])
    return sorted((c for c, p in probabilities.items() if float(p) >= thr),
                  key=lambda c: -float(probabilities[c]))

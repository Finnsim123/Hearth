"""Prediction churn control — a retrain must not rewrite what already worked.

Nightly retrains create a UX failure mode accuracy metrics don't see: the new
model is statistically "as good", but it disagrees with the old one on a big
slice of recent windows, so the dashboard's timeline visibly rewrites and the
user's trust drops ("yesterday it knew I was cooking at six — now it doesn't").
Fard et al. (NeurIPS 2016, "Launch and Iterate") named this prediction churn;
Yan et al. (CVPR 2021, "Positive-Congruent Training") showed new models can
regress on examples the old model got RIGHT (negative flips) even when overall
accuracy improves, and that anchoring training to the old model's correct
answers reduces those flips at negligible accuracy cost.

Hearth applies both halves, sized for a homelab RF pipeline:

  - PCT as sample weights (pct_weights): windows the live champion currently
    classifies correctly get a modest boost (PCT_BOOST) in the challenger's
    fit — the cheap, estimator-agnostic stand-in for Yan's focal distillation.
    The boost is small on purpose: fixing the champion's mistakes still
    matters more than agreeing with it.

  - A churn veto in the promotion gate: a challenger that passes the "not
    credibly worse" Wilson check but disagrees with the live model on more
    than CHURN_MAX of the newest validation block is held back — UNLESS it is
    decisively better (CI lower bound beats the champion's by DECISIVE_GAIN),
    in which case the rewrite is the point.

Bias note: nfr/pfr compare against labels, and the champion has usually seen
part of the newest block in ITS training (it trained yesterday) — so nfr
flatters the champion. That's why the VETO keys on disagreement (churn), which
needs no correctness claim: champion predictions on recent windows are exactly
what the user is currently looking at, whether or not they're right.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

CHURN_MAX = 0.25       # tolerated rewrite share for a merely-adequate challenger
DECISIVE_GAIN = 0.02   # CI-lower-bound gain that buys the right to churn
PCT_BOOST = 1.25       # weight boost on champion-correct training windows


def churn_metrics(champ_est, new_est, X: pd.DataFrame, y: pd.Series) -> dict:
    """Champion vs challenger on the same rows (the newest val block).
    churn = disagreement share (the visible-rewrite rate);
    nfr   = champion right, challenger wrong (negative flips — Yan 2021);
    pfr   = champion wrong, challenger right (the flips we WANT)."""
    if X is None or len(X) == 0:
        return {}
    champ = champ_est.predict_proba(X).idxmax(axis=1)
    new = new_est.predict_proba(X).idxmax(axis=1)
    yv = y.reindex(X.index)
    out = {"n": int(len(X)),
           "churn": round(float((champ != new).mean()), 4),
           "nfr": round(float(((champ == yv) & (new != yv)).mean()), 4),
           "pfr": round(float(((champ != yv) & (new == yv)).mean()), 4)}
    return out


def pct_weights(champ_est, X: pd.DataFrame, y: pd.Series) -> np.ndarray:
    """Positive-congruent boost: PCT_BOOST where the live champion already
    gets the window right, 1.0 elsewhere. Multiplies into the existing
    recency x suspect weight product."""
    try:
        champ = champ_est.predict_proba(X).idxmax(axis=1)
        return np.where(champ.to_numpy() == y.to_numpy(), PCT_BOOST, 1.0)
    except Exception:
        log.debug("pct weights failed — neutral", exc_info=True)
        return np.ones(len(X))


def churn_allows(new_metrics: dict, cur_metrics: dict,
                 wilson) -> tuple[bool, str]:
    """The veto half of the promotion decision. Returns (allowed, reason).
    No churn measurement (young install, load failure, no champion) -> allowed:
    the veto must never deadlock a cold start."""
    ch = (new_metrics or {}).get("churn") or {}
    churn = ch.get("churn")
    if churn is None or churn <= CHURN_MAX:
        return True, ""
    # churny — only a DECISIVELY better challenger may rewrite the timeline;
    # same metric ladder as the gate (gold first, then confirmed)
    for key, n_key in (("accuracy_gold", "n_gold"),
                       ("accuracy_confirmed", "n_confirmed")):
        n_new = (new_metrics or {}).get(n_key, 0)
        n_cur = (cur_metrics or {}).get(n_key, 0)
        if n_new and n_cur:
            new_lo, _ = wilson(round(new_metrics[key] * n_new), n_new)
            cur_lo, _ = wilson(round(cur_metrics[key] * n_cur), n_cur)
            if new_lo >= cur_lo + DECISIVE_GAIN:
                return True, ""
            return False, (f"would rewrite {churn:.0%} of the recent timeline "
                           f"without being decisively better "
                           f"({new_lo:.0%} vs live {cur_lo:.0%} + {DECISIVE_GAIN:.0%})")
    # no comparable human-label metric on both sides: measure exists but the
    # ladder doesn't — hold the churny challenger, keep the stable incumbent
    return False, (f"would rewrite {churn:.0%} of the recent timeline and "
                   "there aren't enough labels to prove it's better")

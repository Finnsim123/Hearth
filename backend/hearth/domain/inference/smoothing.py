"""Temporal smoothing — activities have inertia; raw argmax flickers.

Hysteresis: publish a switch only when the challenger wins k consecutive
windows OR wins once with a big margin. Raw and smoothed are both stored.
"""
from __future__ import annotations

from ..schemas import Prediction


def smooth(history: list[Prediction], current_predicted: str,
           current_confidence: float, k: int = 2, margin: float = 0.25) -> str:
    """history: recent predictions, NEWEST FIRST. Returns smoothed slug."""
    if not history:
        return current_predicted
    published = history[0].smoothed or history[0].predicted
    if current_predicted == published:
        return published
    if current_confidence >= (history[0].confidence + margin):
        return current_predicted                       # decisive win
    recent_raw = [p.predicted for p in history[:k - 1]]
    if len(recent_raw) == k - 1 and all(r == current_predicted for r in recent_raw):
        return current_predicted                       # k consecutive wins
    return published                                   # hold the line


# ── learned transitions (forward filter) ──────────────────────────────────
# Hand-rule hysteresis is kept for the `smoothed` field; this learns a
# transition matrix from the household's OWN label history and forward-
# filters the classifier's probability stream through it — the literature's
# standard fix for per-window flicker and transition-boundary errors.

UNIFORM_MIX = 0.15  # escape hatch: never let the prior fully lock a state in


def _normalize(counts: dict) -> dict:
    return {a: {b: v / sum(row.values()) for b, v in row.items()}
            for a, row in counts.items()}


def learn_transitions(labels) -> dict:
    """Coarse label series (time-indexed, 30-min grid) → row-stochastic
    transition dict {from: {to: p}} with Laplace smoothing. Only counts
    CONSECUTIVE windows (gaps don't vote)."""
    states = sorted(set(labels.dropna().unique()))
    counts = {a: {b: 1.0 for b in states} for a in states}      # Laplace α=1
    idx = labels.index
    for i in range(1, len(labels)):
        if (idx[i] - idx[i - 1]).total_seconds() == 1800:
            a, b = labels.iloc[i - 1], labels.iloc[i]
            if a in counts and b in counts[a]:
                counts[a][b] += 1.0
    return _normalize(counts)


def learn_transitions_by_daypart(labels, tz: str = "UTC") -> dict:
    """Time-conditioned transitions (audit F6): one matrix per part-of-day, so
    sleeping→cooking can be rare at 3am yet plausible at 8am. Returns
    {"0".."3": matrix, "all": matrix}; bucketed by the DESTINATION window's local
    daypart. "all" is the stationary fallback when a bucket is sparse."""
    from zoneinfo import ZoneInfo

    from ..features.pipeline import _bucket
    states = sorted(set(labels.dropna().unique()))
    if not states:
        return {}
    try:
        local_hours = labels.index.tz_convert(ZoneInfo(tz)).hour
    except Exception:
        local_hours = labels.index.hour
    buckets = [int(_bucket(int(h))) for h in local_hours]
    fresh = lambda: {a: {b: 1.0 for b in states} for a in states}   # Laplace α=1
    by = {b: fresh() for b in range(4)}
    allm = fresh()
    idx = labels.index
    for i in range(1, len(labels)):
        if (idx[i] - idx[i - 1]).total_seconds() == 1800:
            a, b = labels.iloc[i - 1], labels.iloc[i]
            if a in allm and b in allm[a]:
                allm[a][b] += 1.0
                by[buckets[i]][a][b] += 1.0
    out = {str(b): _normalize(by[b]) for b in range(4)}
    out["all"] = _normalize(allm)
    return out


def _is_daypart(trans: dict) -> bool:
    """True if `trans` is daypart-keyed ({daypart: matrix}) vs a flat matrix."""
    sample = next(iter(trans.values()), None)
    return (isinstance(sample, dict) and bool(sample)
            and isinstance(next(iter(sample.values())), dict))


def _select_matrix(trans: dict, daypart) -> dict:
    if not _is_daypart(trans):
        return trans
    if daypart is not None and str(daypart) in trans:
        return trans[str(daypart)]
    return trans.get("all", {})


def transition_filter(probs_row, prev_state: str | None, trans: dict | None,
                      daypart=None):
    """One forward-filter step: classifier probs × learned prior given the
    previous state (mixed with uniform). Accepts a flat matrix OR a daypart-keyed
    one (then `daypart` picks the time-conditioned matrix). Returns a
    renormalized copy."""
    if not trans:
        return probs_row
    mat = _select_matrix(trans, daypart)
    if prev_state not in mat:
        return probs_row
    classes = list(probs_row.index)
    n = len(classes)
    prior = [(1 - UNIFORM_MIX) * mat[prev_state].get(c, 1.0 / n)
             + UNIFORM_MIX / n for c in classes]
    blended = probs_row * prior
    total = float(blended.sum())
    return blended / total if total > 0 else probs_row


def viterbi(emissions: list[dict], trans: dict) -> list:
    """Offline most-likely state SEQUENCE (audit F6). For relabeling HISTORY the
    future is available, so full Viterbi gives a globally consistent path that
    fixes transition-boundary errors better than the online forward filter.
    `emissions`: per-window {state: prob}. `trans`: flat matrix (daypart dicts
    collapse to "all"). Returns one state per window."""
    import math

    if not emissions:
        return []
    mat = _select_matrix(trans, None) if trans else {}
    states = list(emissions[0].keys())
    eps = 1e-9
    lg = lambda x: math.log(max(x, eps))
    n = len(states)

    def tp(a, b):  # transition prob a→b, uniform escape when unseen
        return (1 - UNIFORM_MIX) * mat.get(a, {}).get(b, 1.0 / n) + UNIFORM_MIX / n

    V = [{s: lg(emissions[0].get(s, eps)) for s in states}]
    back: list[dict] = [{}]
    for t in range(1, len(emissions)):
        Vt, bt = {}, {}
        for s in states:
            prev = max(states, key=lambda p: V[t - 1][p] + lg(tp(p, s)))
            Vt[s] = V[t - 1][prev] + lg(tp(prev, s)) + lg(emissions[t].get(s, eps))
            bt[s] = prev
        V.append(Vt)
        back.append(bt)
    last = max(states, key=lambda s: V[-1][s])
    path = [last]
    for t in range(len(emissions) - 1, 0, -1):
        last = back[t][last]
        path.insert(0, last)
    return path


def viterbi_relabel(probs, trans: dict):
    """Viterbi over a probability DataFrame (rows=windows, cols=states) → a Series
    of the globally-consistent state per window, aligned to probs.index. For bulk
    history relabeling (Patterns page / range labelling)."""
    import pandas as pd

    if probs.empty:
        return pd.Series(dtype=object)
    emissions = [row.to_dict() for _, row in probs.iterrows()]
    return pd.Series(viterbi(emissions, trans), index=probs.index)

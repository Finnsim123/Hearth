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


# ── duration awareness (HSMM-lite) ─────────────────────────────────────────
# A plain HMM's self-transition is CONSTANT: after 20 minutes of "cooking" the
# prior to stay is the same as after 3 hours — so either blips survive (prior
# too weak) or real transitions lag (prior too strong). Semi-Markov models fix
# this by modeling state DURATION explicitly; van Kasteren et al. (2010) showed
# duration modeling beats plain HMMs on exactly this kind of home-sensor data.
# Full HSMM inference is overkill here — the cheap form is a duration-dependent
# diagonal: replace the matrix's self-transition with 1 − h(d), where h(d) is
# the empirical hazard (P(run ends now | lasted d)) from the household's OWN
# completed runs, and rescale the off-diagonal proportionally.

MAX_RUN = 48        # run-length cap: 24 h of 30-min windows
MIN_RUNS = 5        # completed runs needed before a hazard is trusted
HAZARD_ALPHA = 1.0  # Laplace smoothing on the hazard estimate
STAY_MIN, STAY_MAX = 0.05, 0.98   # the diagonal never hits 0 or 1


def learn_durations(labels) -> dict:
    """Completed-run-length histogram per activity from the 30-min label grid:
    {activity: {str(d): count}}. A run only counts when its END is observed
    (the label changed on a contiguous grid); runs cut off by a gap or the
    series edge are censored — dropping them biases durations slightly short,
    which errs toward LESS stickiness, the safe direction for blip control."""
    idx = labels.index
    out: dict[str, dict[str, int]] = {}
    run_label, run_len = None, 0
    for i in range(len(labels)):
        lab = labels.iloc[i]
        contiguous = i > 0 and (idx[i] - idx[i - 1]).total_seconds() == 1800
        if run_label is not None and contiguous and lab == run_label:
            run_len = min(run_len + 1, MAX_RUN)
            continue
        if run_label is not None and contiguous and lab != run_label:
            # observed end -> the completed run votes
            hist = out.setdefault(str(run_label), {})
            hist[str(run_len)] = hist.get(str(run_len), 0) + 1
        # gap (not contiguous) -> censored: the old run is discarded unseen
        run_label, run_len = lab, 1
    return out          # the final, still-open run is censored too


def duration_hazard(hist: dict, run_len: int) -> float | None:
    """h(d) = P(run ends at d | lasted ≥ d), Laplace-smoothed. None when the
    activity has too few completed runs — caller keeps the stationary prior."""
    try:
        counts = {int(d): int(c) for d, c in hist.items()}
    except Exception:
        return None
    if sum(counts.values()) < MIN_RUNS:
        return None
    ends_now = counts.get(run_len, 0)
    survivors = sum(c for d, c in counts.items() if d >= run_len)
    # beyond the longest observed run: survivors=0 -> h = α/2α = 0.5, steady
    # pressure to leave rather than a hard eviction
    return (ends_now + HAZARD_ALPHA) / (survivors + 2 * HAZARD_ALPHA)


def duration_adjusted_row(mat_row: dict, prev_state: str, hazard: float) -> dict:
    """Transition row with the diagonal set from the hazard (1−h, clamped) and
    the off-diagonal rescaled proportionally — leave probability grows as the
    run outlives its typical duration, and WHERE it goes still follows the
    learned matrix."""
    stay = min(max(1.0 - hazard, STAY_MIN), STAY_MAX)
    off = {c: v for c, v in mat_row.items() if c != prev_state}
    off_total = sum(off.values())
    if off_total <= 0:
        return mat_row
    out = {c: (1.0 - stay) * v / off_total for c, v in off.items()}
    out[prev_state] = stay
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
                      daypart=None, durations: dict | None = None,
                      run_len: int | None = None):
    """One forward-filter step: classifier probs × learned prior given the
    previous state (mixed with uniform). Accepts a flat matrix OR a daypart-keyed
    one (then `daypart` picks the time-conditioned matrix). With `durations`
    (learn_durations output) and `run_len` (windows already spent in
    prev_state) the self-transition becomes duration-dependent (HSMM-lite).
    Returns a renormalized copy."""
    if not trans:
        return probs_row
    mat = _select_matrix(trans, daypart)
    if prev_state not in mat:
        return probs_row
    mat_row = mat[prev_state]
    if durations and run_len:
        h = duration_hazard(durations.get(prev_state) or {}, min(run_len, MAX_RUN))
        if h is not None:
            mat_row = duration_adjusted_row(mat_row, prev_state, h)
    classes = list(probs_row.index)
    n = len(classes)
    prior = [(1 - UNIFORM_MIX) * mat_row.get(c, 1.0 / n)
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

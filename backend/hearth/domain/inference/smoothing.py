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
    return {a: {b: v / sum(row.values()) for b, v in row.items()}
            for a, row in counts.items()}


def transition_filter(probs_row, prev_state: str | None, trans: dict | None):
    """One forward-filter step: classifier probs × learned prior given the
    previous state (mixed with uniform). Returns a renormalized copy."""
    if not trans or prev_state not in trans:
        return probs_row
    classes = list(probs_row.index)
    n = len(classes)
    prior = [(1 - UNIFORM_MIX) * trans[prev_state].get(c, 1.0 / n)
             + UNIFORM_MIX / n for c in classes]
    blended = probs_row * prior
    total = float(blended.sum())
    return blended / total if total > 0 else probs_row

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

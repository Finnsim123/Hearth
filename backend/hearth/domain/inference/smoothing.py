"""Temporal smoothing — activities have inertia; raw argmax flickers.

v1: hysteresis — switch the published activity only when the new class wins
for k consecutive windows OR with margin > m (both configurable). The raw and
smoothed values are both stored; HA entities expose smoothed by default.
Later: HMM transition prior learned from label sequences (RESEARCH.md P4).
"""
from __future__ import annotations

from ..schemas import Prediction


def smooth(history: list[Prediction], current: Prediction, k: int = 2, margin: float = 0.25) -> str:
    """Returns the smoothed activity slug for `current`."""
    raise NotImplementedError

"""Per-role feature extractors.

Phase 1 ports the proven recipes from the prototype: presence fractions,
bed-voltage sentinels, power thresholds, env means/deltas, media states,
person/zone one-hots, alarm-delta, composites. Each function is pure:
(1-min series, binding) -> {suffix: value}. See docs/DATA_MODEL.md §3 for the
full feature table.
"""
from __future__ import annotations

import pandas as pd

from ..schemas import Binding


def presence(series: pd.Series, binding: Binding) -> dict[str, float]:
    """-> _frac (mean occupancy), _any, _transitions."""
    raise NotImplementedError


def bed(series: pd.Series, binding: Binding) -> dict[str, float]:
    """Raw voltage -> _mean, _max, _occupied (options.threshold, default 1.5 V).
    Missing sensor imputes -1 (sentinel: 'not installed' ≠ 'empty')."""
    raise NotImplementedError


def power(series: pd.Series, binding: Binding) -> dict[str, float]:
    """-> _on (max > options.on_threshold), _max_w, _delta_kwh."""
    raise NotImplementedError


def light(series: pd.Series, binding: Binding) -> dict[str, float]:
    """on/off strings -> _on_last, _on_frac."""
    raise NotImplementedError


def media(series: pd.Series, binding: Binding) -> dict[str, float]:
    """playing/paused/idle/off or client counts -> _playing, _paused, _active."""
    raise NotImplementedError


def env(series: pd.Series, binding: Binding) -> dict[str, float]:
    """Numeric environment (CO2, PM2.5, temp, humidity) -> _mean, _delta, _max."""
    raise NotImplementedError


def person(series: pd.Series, binding: Binding) -> dict[str, float]:
    """home/not_home/zone-slug -> _home_last + zone-category one-hots
    (categories defined per person in the UI). Slow sensor: 7-d lookback."""
    raise NotImplementedError


def alarm_time(series: pd.Series, binding: Binding) -> dict[str, float]:
    """HH:MM:SS -> minutes_until (wrapped ±720), imminent (within options.window)."""
    raise NotImplementedError


def composites(features: pd.DataFrame, bindings: list[Binding]) -> pd.DataFrame:
    """Cross-binding features declared in recipe config: lights_off+in_bed,
    media+sofa, fumes+kitchen_presence, partner-context, lag columns.
    Runs after all per-binding extractors."""
    raise NotImplementedError

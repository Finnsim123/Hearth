"""Per-role feature extractors — generic, deterministic, zero entity names.

Each extractor: (1-min series within ONE window, binding) -> {suffix: value}.
Returned values may be NaN (impute() resolves them via role absence
semantics). Ported from the har prototype's proven aggregations, generalized:
thresholds come from binding.options, never constants tied to a device.
"""
from __future__ import annotations

import math

import pandas as pd

from ..schemas import Binding


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def presence(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    if s.empty:
        return {"frac": math.nan, "any": math.nan, "transitions": math.nan}
    on = (s > 0).astype(float)
    return {"frac": float(on.mean()), "any": float(on.max()),
            "transitions": float((on.diff().abs() > 0).sum())}


def bed(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    thr = float(b.options.get("threshold", 1.5))
    if s.empty:  # sentinel -1 applied by impute(): "no sensor" != "empty bed"
        return {"mean": math.nan, "max": math.nan, "occupied": math.nan}
    return {"mean": float(s.mean()), "max": float(s.max()),
            "occupied": float(s.max() > thr)}


def power(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    thr = float(b.options.get("on_threshold", 10.0))
    if s.empty:
        return {"on": math.nan, "max_w": math.nan}
    return {"on": float(s.max() > thr), "max_w": float(s.max())}


def light(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    if s.empty:
        return {"on_last": math.nan, "on_frac": math.nan}
    on = (s > 0).astype(float)
    return {"on_last": float(on.iloc[-1]), "on_frac": float(on.mean())}


def media(series: pd.Series, b: Binding) -> dict[str, float]:
    s = series.dropna().astype(str)
    if s.empty:
        return {"playing": math.nan, "paused": math.nan, "active": math.nan}
    nums = pd.to_numeric(s, errors="coerce")
    if nums.notna().all():  # client-count style entity
        return {"playing": float((nums > 0).any()), "paused": 0.0,
                "active": float((nums > 0).any())}
    return {"playing": float((s == "playing").any()),
            "paused": float((s == "paused").any()),
            "active": float(s.isin(["playing", "paused"]).any())}


def env(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    if s.empty:
        return {"mean": math.nan, "delta": math.nan, "max": math.nan}
    return {"mean": float(s.mean()), "delta": float(s.iloc[-1] - s.iloc[0]),
            "max": float(s.max())}


def person(series: pd.Series, b: Binding) -> dict[str, float]:
    s = series.dropna().astype(str)
    if s.empty:
        return {"home_last": math.nan, "home_frac": math.nan}
    home = (s == "home").astype(float)
    return {"home_last": float(home.iloc[-1]), "home_frac": float(home.mean())}


def focus(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    return {"on_last": math.nan if s.empty else float((s.iloc[-1] > 0))}


def alarm_time(series: pd.Series, b: Binding) -> dict[str, float]:
    """'HH:MM[:SS]' or 'YYYY-MM-DD HH:MM:SS' -> minutes_until next occurrence,
    wrapped to [-720, 720] relative to the WINDOW END (the local-time context
    is injected by the pipeline as series.attrs['window_end_local_minutes'])."""
    s = series.dropna().astype(str)
    if s.empty:
        return {"minutes_until": math.nan, "imminent": math.nan}
    raw = s.iloc[-1].split(" ")[-1]
    try:
        parts = raw.split(":")
        alarm_min = int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return {"minutes_until": math.nan, "imminent": math.nan}
    now_min = series.attrs.get("window_end_local_minutes", 0)
    delta = ((alarm_min - now_min + 720) % 1440) - 720
    window = float(series.attrs.get("imminent_window", 40))
    return {"minutes_until": float(delta), "imminent": float(-10 <= delta <= window)}


def door(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    if s.empty:
        return {"opened_any": math.nan, "open_count": math.nan}
    on = (s > 0).astype(float)
    return {"opened_any": float(on.max()),
            "open_count": float((on.diff() > 0).sum() + (1 if on.iloc[0] > 0 else 0))}


def steps(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    return {"delta": math.nan if len(s) < 2 else float(max(s.iloc[-1] - s.iloc[0], 0.0))}


def battery(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    return {"delta": math.nan if len(s) < 2 else float(s.iloc[-1] - s.iloc[0])}


def custom(series: pd.Series, b: Binding) -> dict[str, float]:
    s = _num(series).dropna()
    if s.empty:
        return {"mean": math.nan, "max": math.nan, "delta": math.nan}
    return {"mean": float(s.mean()), "max": float(s.max()),
            "delta": float(s.iloc[-1] - s.iloc[0])}

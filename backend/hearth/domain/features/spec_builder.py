"""Spec-driven feature builder — executes a validated FeatureSpec.

The deterministic half of the LLM data-analytics layer (llm_layer_design §d):
given a validated spec, compute its feature columns with NO LLM and NO eval, just
parameterised calls to vetted executor functions keyed on the transform id from
features/transforms.py.

This module is ISOLATED: the live window builder (features/pipeline.py) does not
import it yet. Wiring it in (behind the existing recipe path, so no regression)
and hashing the spec into feature_set_version is a later commit. Here we build a
correct, unit-tested capability in isolation.

Coverage note: executors are implemented for the unambiguous numeric/gate
transforms and the boolean composites. A few transforms need richer handling
(categorical one-hot, sub-window sequence/room-transition timing) and are not yet
executable; build_features_from_spec records them in `skipped` rather than
emitting a wrong or silent column.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..schemas import FeatureSpec

log = logging.getLogger(__name__)

DEFAULT_WINDOW = timedelta(minutes=30)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").dropna()


# ── per-entity executors: (window_series, params, window_end) -> float ───────
def _occupancy_fraction(s, p, we):
    n = _num(s)
    return float((n > 0.5).mean()) if len(n) else 0.0


def _any_active(s, p, we):
    n = _num(s)
    return 1.0 if len(n) and bool((n > 0.5).any()) else 0.0


def _transition_count(s, p, we):
    v = s.dropna().to_numpy()
    return float((v[1:] != v[:-1]).sum()) if len(v) > 1 else 0.0


def _run_length_on(s, p, we):
    arr = (_num(s) > 0.5).astype(int).to_numpy()
    best = cur = 0
    for x in arr:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return float(best)        # minutes (1-min grid)


def _time_since_last_change(s, p, we):
    cap = float(p.get("cap_min", 240))
    v = s.dropna()
    if len(v) < 2:
        return cap
    last_change = v.index[0]
    prev = v.iloc[0]
    for ts, val in v.items():
        if val != prev:
            last_change, prev = ts, val
    mins = (we - last_change).total_seconds() / 60.0
    return float(min(max(mins, 0.0), cap))


def _window_mean(s, p, we):
    n = _num(s)
    return float(n.mean()) if len(n) else 0.0


def _window_max(s, p, we):
    n = _num(s)
    return float(n.max()) if len(n) else 0.0


def _window_minimum(s, p, we):
    n = _num(s)
    return float(n.min()) if len(n) else 0.0


def _window_delta(s, p, we):
    n = _num(s)
    return float(n.iloc[-1] - n.iloc[0]) if len(n) else 0.0


def _window_slope(s, p, we):
    n = _num(s)
    if len(n) < 2:
        return 0.0
    x = np.arange(len(n), dtype=float)        # 1-min grid -> position ≈ minutes
    try:
        return float(np.polyfit(x, n.to_numpy(dtype=float), 1)[0])
    except Exception:
        return 0.0


def _counter_rate(s, p, we):
    n = _num(s)
    if len(n) < 2:
        return 0.0
    delta = float(n.iloc[-1] - n.iloc[0])
    if delta < 0:                              # counter reset -> ignore this window
        return 0.0
    minutes = (n.index[-1] - n.index[0]).total_seconds() / 60.0
    return delta / minutes if minutes > 0 else 0.0


def _state_dwell_fraction(s, p, we):
    target = str(p.get("state", ""))
    v = s.dropna().astype(str)
    return float((v == target).mean()) if len(v) else 0.0


_ENTITY_EXEC = {
    "occupancy_fraction": _occupancy_fraction,
    "any_active": _any_active,
    "transition_count": _transition_count,
    "run_length_on": _run_length_on,
    "time_since_last_change": _time_since_last_change,
    "window_mean": _window_mean,
    "window_max": _window_max,
    "window_minimum": _window_minimum,
    "window_delta": _window_delta,
    "window_slope": _window_slope,
    "counter_rate": _counter_rate,
    "home_fraction": _occupancy_fraction,      # same op over a long lookback
    "state_dwell_fraction": _state_dwell_fraction,
}


# ── composite executors: (row_so_far, input_names, params) -> float ──────────
def _co_occurrence_and(row, inputs, p):
    th = float(p.get("threshold", 0.5))
    return 1.0 if inputs and all(row.get(i, 0.0) >= th for i in inputs) else 0.0


def _co_occurrence_count(row, inputs, p):
    return float(sum(1 for i in inputs if row.get(i, 0.0) > 0.0))


def _absence_and(row, inputs, p):
    return 1.0 if inputs and all(row.get(i, 0.0) < 0.5 for i in inputs) else 0.0


_COMPOSITE_EXEC = {
    "co_occurrence_and": _co_occurrence_and,
    "co_occurrence_count": _co_occurrence_count,
    "absence_and": _absence_and,
}

IMPLEMENTED = set(_ENTITY_EXEC) | set(_COMPOSITE_EXEC)


def load_active_spec(repo) -> FeatureSpec | None:
    """The active, VALIDATED feature spec from the 'feature_spec' setting, or
    None if absent/empty/invalid. Validation here means only safe, executable
    features ever reach the builder, regardless of how the setting was written.
    The whitelist mode comes from the 'feature.power_mode' setting."""
    try:
        raw = repo.get_setting("feature_spec")
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw.get("features"):
        return None
    try:
        spec = FeatureSpec.model_validate(raw)
    except Exception:
        log.warning("feature_spec setting failed to parse — ignoring")
        return None
    from .transforms import active_mode
    from .validate import validate_spec
    clean, rejected = validate_spec(spec, mode=active_mode(repo))
    if rejected:
        log.warning("feature_spec: %d features rejected by validation: %s",
                    len(rejected), [n for n, _ in rejected][:8])
    return clean if clean.features else None


def build_features_from_spec(
    prepared: pd.DataFrame, spec: FeatureSpec, grid: list[datetime], *,
    entity_to_col: dict[str, str], window: timedelta = DEFAULT_WINDOW,
) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """Execute `spec.features` over the 1-min `prepared` frame for each window
    start in `grid`. `entity_to_col` maps an entity id to its prepared column
    (binding name). Per-feature `window_min` is honoured as the lookback ending
    at the shared window end. Returns (features_df indexed by window start,
    skipped[(name, reason)]). Composites read features computed earlier in the
    same window, so the spec must be validated (ordered) first."""
    skipped: list[tuple[str, str]] = []
    impl_features = []
    for f in spec.features:
        if f.transform in IMPLEMENTED:
            impl_features.append(f)
        else:
            skipped.append((f.name, f"no executor for transform '{f.transform}'"))

    rows: list[dict[str, float]] = []
    has_data = not prepared.empty
    for ws in grid:
        we = ws + window
        row: dict[str, float] = {}
        for f in impl_features:
            if f.transform in _COMPOSITE_EXEC:
                row[f.name] = _COMPOSITE_EXEC[f.transform](row, f.inputs, f.params)
                continue
            col = entity_to_col.get(f.inputs[0]) if f.inputs else None
            if has_data and col and col in prepared.columns:
                wlen = timedelta(minutes=f.window_min) if f.window_min else window
                start = we - wlen
                sl = prepared.loc[(prepared.index >= start) & (prepared.index < we), col]
            else:
                sl = pd.Series(dtype=float)
            row[f.name] = float(_ENTITY_EXEC[f.transform](sl, f.params, we))
        rows.append(row)

    cols = [f.name for f in impl_features]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(grid, name="window_start"),
                      columns=cols)
    return df, skipped

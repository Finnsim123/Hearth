"""Window builder — pillar 1's engine. Pure functions + one orchestrator.

raw (1-min, role-aware ffill) -> per-binding recipes -> composites (data AST)
-> lag columns -> impute (role absence semantics) -> feature store.
Identical code path feeds training matrices and live inference rows (ADR-7).
Nothing here knows an entity name; everything is keyed on Binding.role.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from ..schemas import Binding, Role
from .composites import apply_composites
from .registry import all_recipes, feature_set_version, recipe_for

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=30)

# Event-dynamics features (CASAS baseline: Cook & Krishnan, "Activity
# Recognition on Streaming Sensor Data"): event COUNT, dominant sensor and
# IDLENESS discriminate activities better than aggregates alone — 40 silent
# minutes at 23:30 says "sleeping" louder than any CO2 curve.
EVENT_ROLES = {Role.PRESENCE, Role.DOOR, Role.MEDIA}
IDLE_CAP_MIN = 240.0  # read_raw lookback bounds what we can know


def event_dynamics(prepared: pd.DataFrame, bindings: list[Binding]):
    """Precompute, on the 1-min grid: per-column change masks for direct
    event sensors + a running 'minutes since ANY direct event' series."""
    cols = [b.name for b in bindings
            if b.role in EVENT_ROLES and b.name in prepared.columns
            and pd.api.types.is_numeric_dtype(prepared[b.name])]
    if not cols or prepared.empty:
        return None
    changes = prepared[cols].diff().abs().gt(0.5)
    any_change = changes.any(axis=1)
    # timestamp of the most recent direct event, forward-filled
    ts = pd.Series(prepared.index, index=prepared.index)
    last_event = ts.where(any_change).ffill()
    idle_min = ((ts - last_event).dt.total_seconds() / 60).clip(upper=IDLE_CAP_MIN)
    return {"cols": cols, "changes": changes, "idle_min": idle_min}


def prepare(raw: pd.DataFrame, bindings: list[Binding]) -> pd.DataFrame:
    """1-min resample + per-ROLE forward-fill limits (role metadata, ADR-8)."""
    if raw.empty:
        return raw
    df = raw.resample("1min").last()
    by_name = {b.name: b for b in bindings}
    for col in df.columns:
        b = by_name.get(col)
        if b is None:
            continue
        limit = recipe_for(b.role).ffill_limit_min
        if limit > 0:
            df[col] = df[col].ffill(limit=limit)
    return df


def window_grid(start: datetime, end: datetime, stride_min: int) -> list[datetime]:
    """Window START times whose full 30-min window fits in [start, end]."""
    grid, t = [], start - timedelta(minutes=start.minute % stride_min,
                                    seconds=start.second, microseconds=start.microsecond)
    if t < start:
        t += timedelta(minutes=stride_min)
    while t + WINDOW <= end:
        grid.append(t)
        t += timedelta(minutes=stride_min)
    return grid


def extract_windows(prepared: pd.DataFrame, bindings: list[Binding],
                    grid: list[datetime], tz: str = "UTC") -> pd.DataFrame:
    dyn = event_dynamics(prepared, bindings)
    """One row per window start: temporal features + per-binding recipe outputs
    (columns '{binding.name}_{suffix}'). Person-agnostic — caller filters
    bindings to shared + this person's.

    Perf: for grids aligned to the 30-min boundary (training/fast-track), the
    window slices are precomputed in ONE O(n) groupby pass instead of a boolean
    mask per window (O(n x windows) — this was a multi-hour stage on 90-day
    fast-tracks before)."""
    zone = ZoneInfo(tz)
    aligned = all(g.minute % 30 == 0 and g.second == 0 for g in grid)
    slices: dict[datetime, pd.DataFrame] = {}
    if aligned and not prepared.empty:
        for ws_ts, group in prepared.groupby(prepared.index.floor("30min")):
            slices[ws_ts.to_pydatetime()] = group
    rows = []
    empty_slice = prepared.iloc[0:0]
    for ws in grid:
        we = ws + WINDOW
        if aligned:
            sl = slices.get(ws, empty_slice)
        else:
            sl = prepared.loc[(prepared.index >= ws) & (prepared.index < we)]
        local = ws.astimezone(zone)
        row: dict[str, float] = {
            "hour_of_day": float(local.hour),
            "day_of_week": float(local.weekday()),
            "is_weekend": float(local.weekday() >= 5),
        }
        local_end = we.astimezone(zone)
        end_minutes = local_end.hour * 60 + local_end.minute
        # event dynamics for this window (0/idle-cap when no event sensors)
        if dyn is not None:
            ch = dyn["changes"].loc[(dyn["changes"].index >= ws)
                                    & (dyn["changes"].index < we)] \
                if not aligned else dyn["changes"].reindex(sl.index).fillna(False)
            per_sensor = ch.sum()
            total = float(per_sensor.sum())
            row["evt_count"] = total
            row["evt_active_sensors"] = float((per_sensor > 0).sum())
            row["evt_dominant_share"] = (float(per_sensor.max() / total)
                                         if total > 0 else 0.0)
            idle_at_end = dyn["idle_min"].loc[:we].iloc[-1] \
                if len(dyn["idle_min"].loc[:we]) else IDLE_CAP_MIN
            row["evt_idle_minutes"] = float(idle_at_end if pd.notna(idle_at_end)
                                            else IDLE_CAP_MIN)
        else:
            row["evt_count"] = 0.0
            row["evt_active_sensors"] = 0.0
            row["evt_dominant_share"] = 0.0
            row["evt_idle_minutes"] = IDLE_CAP_MIN
        for b in bindings:
            series = sl[b.name] if b.name in sl.columns else pd.Series(dtype=object)
            series.attrs["window_end_local_minutes"] = end_minutes
            series.attrs["imminent_window"] = float(b.options.get("imminent_window", 40))
            for suffix, value in recipe_for(b.role).fn(series, b).items():
                row[f"{b.name}_{suffix}"] = value
        rows.append(row)
    out = pd.DataFrame(rows, index=pd.DatetimeIndex(grid, name="window_start", tz="UTC"))
    return out


def add_lags(df: pd.DataFrame, lag_features: list[str]) -> pd.DataFrame:
    for feat in lag_features:
        if feat in df.columns:
            df[f"{feat}_lag1"] = df[feat].shift(1)
    return df


def impute(df: pd.DataFrame, bindings: list[Binding]) -> pd.DataFrame:
    """Role-driven semantic imputation. -1 = 'sensor absent' (bed), 0 = 'no
    event' (everything else); lag columns fall back to their base column."""
    df = df.copy()
    for b in bindings:
        recipe = recipe_for(b.role)
        for suffix in recipe.suffixes:
            col = f"{b.name}_{suffix}"
            if col in df.columns:
                df[col] = df[col].fillna(recipe.absence_value)
    for col in [c for c in df.columns if c.endswith("_lag1")]:
        base = col[:-5]
        df[col] = df[col].fillna(df[base]) if base in df.columns else df[col].fillna(0.0)
    df = df.fillna(0.0)  # composites / temporal never NaN, belt-and-braces
    assert not df.isna().any().any(), "NaNs survived imputation"
    return df


def bindings_for_person(all_bindings: list[Binding], person_id: str) -> list[Binding]:
    """Shared sensors + this person's personal sensors."""
    return [b for b in all_bindings if b.enabled and b.person_id in (None, person_id)]


def compute_features(prepared: pd.DataFrame, bindings: list[Binding],
                     grid: list[datetime], tz: str,
                     composites: list[dict], lag_features: list[str]) -> pd.DataFrame:
    """The pure pipeline: extract -> composites -> lags -> impute."""
    df = extract_windows(prepared, bindings, grid, tz)
    df = apply_composites(df, composites)
    df = add_lags(df, lag_features)
    return impute(df, bindings)


def build_windows(tsdb, repo, person_id: str, start: datetime, end: datetime,
                  stride_min: int = 30) -> pd.DataFrame:
    """Orchestrator: read raw -> compute -> persist. Returns the matrix."""
    bindings = bindings_for_person(repo.bindings(), person_id)
    if not bindings:
        return pd.DataFrame()
    composites = repo.get_setting("composites", []) or []
    lag_features = repo.get_setting("lag_features", []) or []
    tz = repo.get_setting("timezone", "UTC") or "UTC"
    raw = tsdb.read_raw(bindings, start - timedelta(minutes=120), end)
    prepared = prepare(raw, bindings) if not raw.empty else raw
    grid = window_grid(start, end, stride_min)
    if not grid:
        return pd.DataFrame()
    feats = compute_features(prepared, bindings, grid, tz, composites, lag_features)
    tsdb.write_features(person_id, feature_set_version(composites), feats)
    return feats


def build_latest_windows(tsdb, repo) -> None:
    """Scheduler entrypoint: build any complete-but-unwritten windows for every
    enabled person, then heartbeat."""
    now = datetime.now(timezone.utc)
    composites = repo.get_setting("composites", []) or []
    fset = feature_set_version(composites)
    for person in repo.persons():
        if not person.enabled:
            continue
        last = tsdb.last_feature_time(person.id, fset)
        start = (last + timedelta(minutes=5)) if last else now - timedelta(hours=2)
        try:
            built = build_windows(tsdb, repo, person.id, start, now, stride_min=5)
            if not built.empty:
                log.info("features: %s +%d windows", person.id, len(built))
        except Exception:
            log.exception("window build failed for %s", person.id)
    tsdb.write_heartbeat("window_builder")

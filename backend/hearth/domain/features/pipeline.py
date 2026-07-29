"""Window builder — pillar 1's engine. Pure functions + one orchestrator.

raw (1-min, role-aware ffill) -> per-binding recipes -> composites (data AST)
-> lag columns -> impute (role absence semantics) -> feature store.
Identical code path feeds training matrices and live inference rows (ADR-7).
Nothing here knows an entity name; everything is keyed on Binding.role.
"""
from __future__ import annotations

import logging
import math
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from ..schemas import Binding, Role
from .composites import apply_composites
from .registry import active_feature_set_version, all_recipes, recipe_for
from .spec_builder import load_active_spec

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=30)           # window END cadence — shared by all roles
DEFAULT_WINDOW_MIN = int(WINDOW.total_seconds() // 60)


def max_window_min(bindings: list[Binding]) -> int:
    """Longest per-role lookback among these bindings — how far read_raw must
    pre-roll so the slowest sensor's first window has full history."""
    from .registry import recipe_for
    return max((recipe_for(b.role).window_min for b in bindings),
               default=DEFAULT_WINDOW_MIN)

# Time encoding. "full" = raw hour_of_day (0-23) — lets a tree memorize a
# per-hour schedule and crowd out sensors (the clock-crutch failure). "coarse"
# (default) = a 4-bucket part-of-day, keeping the legitimate "it's night-ish"
# prior without the lookup table. "none" = no temporal feature at all.
_PART_OF_DAY = "time_bucket"  # 0 night · 1 morning · 2 afternoon · 3 evening


def _bucket(hour: int) -> float:
    if hour < 5 or hour >= 22:
        return 0.0
    if hour < 11:
        return 1.0
    if hour < 17:
        return 2.0
    return 3.0


def time_features(local, granularity: str) -> dict[str, float]:
    if granularity == "none":
        return {}
    if granularity == "full":
        return {"hour_of_day": float(local.hour),
                "day_of_week": float(local.weekday()),
                "is_weekend": float(local.weekday() >= 5)}
    # coarse (default)
    return {_PART_OF_DAY: _bucket(local.hour),
            "is_weekend": float(local.weekday() >= 5)}


# every temporal column name across granularities — discovery/training strip
# these so clustering and importance never key on the clock
TEMPORAL_COLS = ["hour_of_day", "day_of_week", "is_weekend", _PART_OF_DAY]

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


# Home-mobility features (per window, person-level) — the indoor analogue of the
# CDR mobility literature (Björkegren & Grosman; Isaacman et al. 2011): a person's
# movement across the home carries activity signal (roaming vs settled; cooking is
# concentrated in one room, tidying spans several). We treat each room's event
# count as a category count and summarise the DISTRIBUTION — no floor plan or
# coordinates needed. Set-based only (this batch): distinct rooms, concentration,
# spread. Degrades cleanly to zeros on homes with no room labels / one sensor.
MOBILITY_COLS = ["mob_rooms_active", "mob_top_room_frac", "mob_room_entropy",
                 "mob_room_switches"]


def mobility_stats(room_counts: dict[str, float]) -> dict[str, float]:
    """Summarise how the window's activity is spread across rooms.
      mob_rooms_active — distinct rooms with any activity (range).
      mob_top_room_frac — share of activity in the busiest room (0..1; 1 = one room).
      mob_room_entropy — normalised Shannon entropy of the room distribution
                         (0 = all in one room, 1 = evenly spread) — the 'roaming' axis."""
    total = sum(room_counts.values())
    n = len(room_counts)
    if total <= 0 or n == 0:
        return {"mob_rooms_active": 0.0, "mob_top_room_frac": 0.0, "mob_room_entropy": 0.0}
    ps = [c / total for c in room_counts.values() if c > 0]
    ent = (-sum(p * math.log(p) for p in ps) / math.log(n)) if n > 1 else 0.0
    return {"mob_rooms_active": float(n), "mob_top_room_frac": float(max(ps)),
            "mob_room_entropy": float(ent)}


def room_switches(ch: pd.DataFrame, col_to_room: dict[str, str]) -> float:
    """How many times activity moved between rooms across the window, in time
    order — the 'pacing / restlessness' axis (someone cooking stays put; someone
    tidying or pacing hops rooms). `ch` is the window's per-minute × per-sensor
    change mask; each minute's room is the one with the most changes."""
    if ch.empty:
        return 0.0
    cols = [c for c in ch.columns if c in col_to_room]
    if not cols:
        return 0.0
    seq: list[str] = []
    for _, minute in ch[cols].iterrows():
        counts: dict[str, int] = {}
        for col, changed in minute.items():
            if changed:
                rm = col_to_room[col]
                counts[rm] = counts.get(rm, 0) + 1
        if counts:
            seq.append(max(counts, key=counts.get))
    return float(sum(1 for a, b in zip(seq, seq[1:]) if a != b))


# ── anchor-distance (Isaacman-style "distance to points of interest") ─────────
# A person's graph-distance from where they are to meaningful rooms (bed, door)
# is activity signal — and, crucially, SYNTHETIC: distance-to-bed climbing through
# the day and collapsing at night is a sleep cue even in a home with no bed
# occupancy sensor, as long as the bedroom is a labelled room with any sensor.
DIST_CAP = 6.0                                   # hops when unknown/unreachable
_ANCHOR_ROOM_HINTS = {
    "bed": re.compile(r"bed|bedroom|slaap"),                          # nl: slaapkamer
    "door": re.compile(r"\bhall\b|hallway|entr|entree|foyer|porch|voordeur"),
}


def detect_anchors(bindings: list[Binding]) -> dict[str, str]:
    """Map anchor keys → a room, from sensor ROLE first (a BED sensor's room, a
    DOOR sensor's room), then room-NAME hints — so a bedroom with only a motion
    sensor still anchors 'bed' (the point of the synthetic proximity feature)."""
    anchors: dict[str, str] = {}
    for b in bindings:
        if not b.room:
            continue
        if b.role == Role.BED:
            anchors.setdefault("bed", b.room)
        elif b.role == Role.DOOR:
            anchors.setdefault("door", b.room)
    rooms = sorted({b.room for b in bindings if b.room})
    for key, rx in _ANCHOR_ROOM_HINTS.items():
        if key not in anchors:
            for r in rooms:
                if rx.search(r.lower()):
                    anchors[key] = r
                    break
    return anchors


def learn_room_graph(room_seq: list[str]) -> dict[str, list[str]]:
    """Undirected room adjacency from a sequence of dominant rooms: consecutive
    distinct rooms are treated as neighbours (they hand off to each other)."""
    adj: dict[str, set] = {}
    for a, b in zip(room_seq, room_seq[1:]):
        if a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    return {k: sorted(v) for k, v in adj.items()}


def room_distance(graph: dict[str, list[str]], src: str, dst: str) -> int | None:
    """BFS hop distance src→dst on the adjacency graph; None if unreachable."""
    if src == dst:
        return 0
    if src not in graph:
        return None
    seen, q = {src}, deque([(src, 0)])
    while q:
        node, d = q.popleft()
        for nb in graph.get(node, ()):  # type: ignore[arg-type]
            if nb == dst:
                return d + 1
            if nb not in seen:
                seen.add(nb)
                q.append((nb, d + 1))
    return None


def anchor_distances(graph: dict[str, list[str]], anchors: dict[str, str],
                     dominant_room: str | None) -> dict[str, float]:
    """dist_to_<anchor> hops from the window's busiest room to each anchor room;
    DIST_CAP when the room is unknown (no activity) or unreachable."""
    out: dict[str, float] = {}
    for key, room in anchors.items():
        d = None if dominant_room is None else room_distance(graph, dominant_room, room)
        out[f"dist_to_{key}"] = float(d) if d is not None else DIST_CAP
    return out


def refresh_room_graph(tsdb, repo, days: int = 14) -> dict[str, list[str]]:
    """Learn + cache the home's room-adjacency graph from recent presence history
    (settings key `room.graph`). Cheap-ish and slow-changing, so callers throttle
    it to ~daily. Returns the graph (also cached)."""
    binds = [b for b in repo.bindings()
             if getattr(b, "enabled", True) and b.room and b.role in EVENT_ROLES]
    if not binds:
        return {}
    end = datetime.now(timezone.utc)
    raw = tsdb.read_raw(binds, end - timedelta(days=days), end)
    if raw is None or raw.empty:
        return {}
    prepared = prepare(raw, binds)
    cols = [b.name for b in binds if b.name in prepared.columns
            and pd.api.types.is_numeric_dtype(prepared[b.name])]
    if not cols:
        return {}
    col_to_room = {b.name: b.room for b in binds}
    changes = prepared[cols].diff().abs().gt(0.5)
    seq: list[str] = []
    for _, minute in changes.iterrows():
        counts: dict[str, int] = {}
        for col, changed in minute.items():
            if changed:
                rm = col_to_room.get(col)
                if rm:
                    counts[rm] = counts.get(rm, 0) + 1
        if counts:
            seq.append(max(counts, key=counts.get))
    graph = learn_room_graph(seq)
    repo.set_setting("room.graph", graph)
    return graph


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
                    grid: list[datetime], tz: str = "UTC",
                    time_granularity: str = "coarse",
                    room_graph: dict[str, list[str]] | None = None,
                    anchors: dict[str, str] | None = None) -> pd.DataFrame:
    """One row per window start: temporal features + per-binding recipe outputs
    (columns '{binding.name}_{suffix}'). Person-agnostic — caller filters
    bindings to shared + this person's.

    Perf: for grids aligned to the 30-min boundary (training/fast-track), the
    window slices are precomputed in ONE O(n) groupby pass instead of a boolean
    mask per window (O(n x windows) — this was a multi-hour stage on 90-day
    fast-tracks before)."""
    dyn = event_dynamics(prepared, bindings)
    col_to_room = {b.name: b.room for b in bindings if b.room}   # mobility grouping
    zone = ZoneInfo(tz)
    aligned = all(g.minute % 30 == 0 and g.second == 0 for g in grid)
    slices: dict[datetime, pd.DataFrame] = {}
    if aligned and not prepared.empty:
        for ws_ts, group in prepared.groupby(prepared.index.floor("30min")):
            slices[ws_ts.to_pydatetime()] = group
    # idleness at each window END, precomputed in ONE asof pass (was an
    # O(n×windows) .loc[:we] re-slice inside the loop — the perf bug the
    # groupby fast-path exists to avoid)
    idle_at_end: dict[datetime, float] = {}
    if dyn is not None:
        we_index = pd.DatetimeIndex([ws + WINDOW for ws in grid])
        idle_series = dyn["idle_min"].reindex(we_index, method="ffill")
        idle_at_end = {ws: (float(v) if pd.notna(v) else IDLE_CAP_MIN)
                       for ws, v in zip(grid, idle_series.to_numpy())}
    rows = []
    empty_slice = prepared.iloc[0:0]
    pidx = prepared.index if not prepared.empty else None   # sorted 1-min grid
    for ws in grid:
        we = ws + WINDOW
        if aligned:
            sl = slices.get(ws, empty_slice)
        else:
            sl = prepared.loc[(prepared.index >= ws) & (prepared.index < we)]
        local = ws.astimezone(zone)
        row: dict[str, float] = dict(time_features(local, time_granularity))
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
            row["evt_idle_minutes"] = idle_at_end.get(ws, IDLE_CAP_MIN)
            # home-mobility: fold this window's per-sensor events into per-room
            # counts, then summarise the spread across rooms
            room_counts: dict[str, float] = {}
            for col, cnt in per_sensor.items():
                rm = col_to_room.get(col)
                if rm and cnt > 0:
                    room_counts[rm] = room_counts.get(rm, 0.0) + float(cnt)
            row.update(mobility_stats(room_counts))
            row["mob_room_switches"] = room_switches(ch, col_to_room)
            if anchors:
                dominant = max(room_counts, key=room_counts.get) if room_counts else None
                row.update(anchor_distances(room_graph or {}, anchors, dominant))
        else:
            row["evt_count"] = 0.0
            row["evt_active_sensors"] = 0.0
            row["evt_dominant_share"] = 0.0
            row["evt_idle_minutes"] = IDLE_CAP_MIN
            row.update(mobility_stats({}))
            row["mob_room_switches"] = 0.0
            if anchors:
                row.update(anchor_distances(room_graph or {}, anchors, None))
        for b in bindings:
            recipe = recipe_for(b.role)
            # role-aware lookback: same window END (we), per-role start. Default
            # roles reuse the shared 30-min slice (fast path, no extra work);
            # off-default roles take a searchsorted slice [we - window, we).
            if recipe.window_min == DEFAULT_WINDOW_MIN or pidx is None:
                bsl = sl
            else:
                hi = pidx.searchsorted(pd.Timestamp(we))
                lo = pidx.searchsorted(pd.Timestamp(we - timedelta(minutes=recipe.window_min)))
                bsl = prepared.iloc[lo:hi]
            series = bsl[b.name] if b.name in bsl.columns else pd.Series(dtype=object)
            series.attrs["window_end_local_minutes"] = end_minutes
            series.attrs["imminent_window"] = float(b.options.get("imminent_window", 40))
            for suffix, value in recipe.fn(series, b).items():
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
    # Missingness indicators (Björkegren & Grosman): flag when a binding produced
    # NO value this window, computed BEFORE the fill below — so the model can tell
    # "sensor observed absent/off" from "no reading at all", instead of reading an
    # imputed sentinel as real signal. One flag per binding (not per suffix) to
    # keep the feature count modest on small-data homes.
    for b in bindings:
        recipe = recipe_for(b.role)
        cols = [f"{b.name}_{s}" for s in recipe.suffixes if f"{b.name}_{s}" in df.columns]
        if cols:
            df[f"{b.name}_missing"] = df[cols].isna().all(axis=1).astype(float)
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
                     composites: list[dict], lag_features: list[str],
                     time_granularity: str = "coarse", spec=None,
                     room_graph: dict[str, list[str]] | None = None,
                     anchors: dict[str, str] | None = None) -> pd.DataFrame:
    """The pure pipeline: extract -> composites -> lags -> (spec features) ->
    impute. `spec` is the active FeatureSpec or None; when None the output is
    exactly the historical recipe pipeline (no regression). Spec columns are
    added alongside recipe columns (never overwriting them) and imputed to 0."""
    df = extract_windows(prepared, bindings, grid, tz, time_granularity,
                         room_graph=room_graph, anchors=anchors)
    df = apply_composites(df, composites)
    df = add_lags(df, lag_features)
    if spec is not None and getattr(spec, "features", None):
        from .spec_builder import build_features_from_spec
        e2c = {b.entity_id: b.name for b in bindings}
        spec_df, skipped = build_features_from_spec(
            prepared, spec, grid, entity_to_col=e2c, window=WINDOW)
        if skipped:
            log.warning("spec features skipped (no executor): %s",
                        [n for n, _ in skipped])
        # positional add (same grid order); recipe columns win any name clash
        for col in spec_df.columns:
            if col not in df.columns:
                df[col] = spec_df[col].to_numpy()
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
    tg = repo.get_setting("time_granularity", "coarse") or "coarse"
    spec = load_active_spec(repo)
    room_graph = repo.get_setting("room.graph") or {}
    anchors = detect_anchors(bindings)             # dist_to_<anchor> features
    preroll = max(120, max_window_min(bindings))   # cover the slowest role's lookback
    raw = tsdb.read_raw(bindings, start - timedelta(minutes=preroll), end)
    prepared = prepare(raw, bindings) if not raw.empty else raw
    grid = window_grid(start, end, stride_min)
    if not grid:
        return pd.DataFrame()
    feats = compute_features(prepared, bindings, grid, tz, composites, lag_features, tg, spec,
                             room_graph=room_graph, anchors=anchors)
    tsdb.write_features(person_id, active_feature_set_version(repo, spec), feats)
    return feats


def ensure_history(tsdb, repo, person_id: str, start, end, *,
                   have: int = 0, need: int = 100,
                   chunk_days: int = 7, stride_min: int = 30) -> int:
    """Backfill feature windows under the ACTIVE feature-set version.

    Any feature-spec change (approving a new device via integrate, flipping
    the power mode, adding a composite) changes the fset hash — and windows
    are stored per hash, so ALL history instantly stops counting for training:
    build_latest_windows only builds forward from now−2h, the trainer sees
    <min windows and skips, and the first model to clear the bar again is
    trained on hours, not months. This rebuilds the training range from raw
    history, chunked like the fast track so peak memory stays flat.

    Cold-start guard: only persons with a PROMOTED model qualify — they have
    provably trained before, so this is a rebuild, not fabrication. A fresh
    install with no raw history must keep skipping (empty-window models are
    worse than no model).
    """
    if have >= need:
        return 0
    try:
        if not any(m.promoted for m in repo.models(person_id)):
            return 0
    except Exception:
        return 0
    from math import ceil
    span_days = max(1, ceil((end - start).total_seconds() / 86400))
    n_chunks = ceil(span_days / chunk_days)
    built = 0
    log.info("[%s] rebuilding %d day(s) of feature history under the active "
             "feature set (%d chunks)", person_id, span_days, n_chunks)
    for ci in range(n_chunks):
        cstart = start + timedelta(days=ci * chunk_days)
        cstop = min(cstart + timedelta(days=chunk_days), end)
        try:
            built += len(build_windows(tsdb, repo, person_id, cstart, cstop,
                                       stride_min))
        except Exception:
            log.exception("history rebuild chunk %d/%d failed for %s",
                          ci + 1, n_chunks, person_id)
    return built


def build_latest_windows(tsdb, repo) -> None:
    """Scheduler entrypoint: build any complete-but-unwritten windows for every
    enabled person, then heartbeat."""
    now = datetime.now(timezone.utc)
    # keep the home's room-adjacency graph fresh for dist_to_<anchor> features —
    # slow-changing, so at most once a day, before building windows off it.
    try:
        last = repo.get_setting("room.graph.at")
        if not last or (now - datetime.fromisoformat(last)) > timedelta(hours=24):
            refresh_room_graph(tsdb, repo)
            repo.set_setting("room.graph.at", now.isoformat())
    except Exception:
        log.debug("room-graph refresh skipped", exc_info=True)
    fset = active_feature_set_version(repo)
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

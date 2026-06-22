"""Body activity — aggregation of cumulative wearable counters (steps, distance,
floors) for the behaviour dashboard. Independent of the HAR model (these are
DESCRIPTIVE signals, not predictions).

Cumulative counters (InfoTier.CUMULATIVE_COUNTER / Role.STEPS) only matter as a
RATE: the activity in a window is the *increase* of the counter across it. Two
failure modes are handled explicitly:
  - midnight reset → the counter drops to ~0; a negative diff is read as "reset",
    so the window's delta is the new value, not a huge negative number.
  - device not worn → no samples in a window; that window is NOT counted as zero
    activity (absent != sedentary). Coverage tracks worn-vs-not so the UI never
    presents a phone-on-the-charger day as "totally still".

Pure: no I/O. The route reads the bound counters via tsdb.read_raw() and the
activity timeline via read_predictions(), then hands both here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .summary import UNKNOWN, RhythmCell, TrendCallout, _parse, _state, make_callout

# A reset is only believed when the counter falls by more than this fraction of
# its previous value — guards against tiny non-monotonic jitter being mistaken
# for a midnight rollover.
RESET_DROP_FRAC = 0.5

# Steps accrued in a window above which it counts as "active" rather than
# "sedentary" (worn-but-still). Coarse by design — a handful of steps is still
# sedentary; real movement clears this easily.
ACTIVE_STEP_WINDOW = 100.0


class BodyDay(BaseModel):
    date: str
    totals: dict[str, float]       # signal label -> amount accrued that day
    covered_min: float             # minutes with sensor data (device worn)
    active_min: float = 0.0        # worn minutes with real movement
    sedentary_min: float = 0.0     # worn minutes, little/no movement


class BodySummary(BaseModel):
    person_id: str
    signals: list[str]             # labels present (e.g. ["steps", "floors"])
    primary: str | None            # the headline signal (steps if present)
    units: dict[str, str]          # label -> unit ("steps", "km", "floors", "")
    total: dict[str, float]        # signal -> total over the range
    coverage: float                # worn windows / total windows in range (0..1)
    worn_min: float                # device worn & reporting movement
    charging_min: float            # on the charger / docked (steps≈0 is EXPECTED)
    absent_min: float              # no data and not charging — genuinely unknown
    active_min: float = 0.0        # worn minutes with real movement (whole range)
    sedentary_min: float = 0.0     # worn minutes, little/no movement
    per_day: list[BodyDay]
    rhythm: list[RhythmCell]       # 24h x dow grid of the PRIMARY signal
    by_activity: dict[str, float]  # activity slug -> primary-signal amount during it
    trends: list[TrendCallout] = []  # WoW callouts for active/sedentary minutes


def _deltas(samples: list[tuple[datetime, float]], window_min: int
            ) -> dict[datetime, float]:
    """Cumulative samples (any order) → {window_start_utc: amount in window}.
    Quantises to the window grid (last sample per window), then diffs with
    reset handling. Windows with no sample are simply absent (not zero)."""
    if not samples:
        return {}
    secs = window_min * 60
    grid: dict[datetime, float] = {}
    for ts, val in sorted(samples, key=lambda s: s[0]):
        if val is None:
            continue
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        bucket = datetime.fromtimestamp((int(ts.timestamp()) // secs) * secs, timezone.utc)
        grid[bucket] = float(val)          # last value wins within a window
    out: dict[datetime, float] = {}
    prev: float | None = None
    for bucket in sorted(grid):
        v = grid[bucket]
        if prev is None:
            prev = v
            continue
        if v < prev - abs(prev) * RESET_DROP_FRAC:
            out[bucket] = max(0.0, v)      # reset: climbed from ~0 to v this window
        else:
            out[bucket] = max(0.0, v - prev)
        prev = v
    return out


def _truthy(v) -> bool:
    """Is a raw charging-sensor sample 'on'? Handles numeric (0/1) and HA string
    states ('on', 'charging', 'full')."""
    if isinstance(v, (int, float)):
        return float(v) > 0.5
    s = str(v).strip().lower()
    return s in {"on", "charging", "true", "1", "yes", "full"}


def _charge_buckets(samples, window_min: int) -> set[datetime]:
    """Window starts (UTC) during which the device was charging/docked."""
    if not samples:
        return set()
    secs = window_min * 60
    out: set[datetime] = set()
    for ts, val in samples:
        if not _truthy(val):
            continue
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        out.add(datetime.fromtimestamp((int(ts.timestamp()) // secs) * secs, timezone.utc))
    return out


def _unit_for(label: str) -> str:
    l = label.lower()
    if "step" in l:
        return "steps"
    if "dist" in l or l.endswith("_km") or "km" in l:
        return "km"
    if "floor" in l or "elev" in l:
        return "floors"
    return ""


def _pick_primary(signals: list[str]) -> str | None:
    for s in signals:
        if "step" in s.lower():
            return s
    return signals[0] if signals else None


def summarize_body(person_id: str, counters: dict[str, list[tuple[datetime, float]]],
                   activity_rows: list[dict], *, tz: str = "UTC",
                   now: datetime | None = None, window_min: int = 30,
                   range_start: datetime | None = None,
                   charging: list[tuple[datetime, object]] | None = None) -> BodySummary:
    zone = ZoneInfo(tz) if tz else timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(zone)

    charge_set = _charge_buckets(charging or [], window_min)
    # a charging/docked window is NOT activity data — steps≈0 there is expected, so
    # drop those windows from the rate deltas entirely (they'd otherwise read as
    # "worn but still" and understate how active the worn time was).
    deltas = {}
    for label, s in counters.items():
        if not s:
            continue
        d = {b: v for b, v in _deltas(s, window_min).items() if b not in charge_set}
        if d:
            deltas[label] = d
    signals = sorted(deltas)
    primary = _pick_primary(signals)

    per_day: dict[str, BodyDay] = {}
    total: dict[str, float] = {}
    rhythm: dict[tuple[int, int], dict[str, float]] = {}
    covered_buckets: set[datetime] = set()
    for label, dmap in deltas.items():
        for bucket, amt in dmap.items():
            local = bucket.astimezone(zone)
            date = local.date().isoformat()
            day = per_day.get(date) or BodyDay(date=date, totals={}, covered_min=0.0)
            day.totals[label] = day.totals.get(label, 0.0) + amt
            per_day[date] = day
            total[label] = total.get(label, 0.0) + amt
            if label == primary:
                cell = rhythm.setdefault((local.weekday(), local.hour), {})
                cell[primary] = cell.get(primary, 0.0) + amt
                if amt >= ACTIVE_STEP_WINDOW:
                    day.active_min += window_min
                else:
                    day.sedentary_min += window_min
            covered_buckets.add(bucket)
    active_min = sum(d.active_min for d in per_day.values())
    sedentary_min = sum(d.sedentary_min for d in per_day.values())

    # covered minutes per day = distinct windows with ANY signal that day
    cov_by_day: dict[str, set[datetime]] = {}
    for b in covered_buckets:
        cov_by_day.setdefault(b.astimezone(zone).date().isoformat(), set()).add(b)
    for date, day in per_day.items():
        day.covered_min = len(cov_by_day.get(date, set())) * window_min

    # coverage over the whole range, split three ways: worn (data, not charging) /
    # charging (docked) / absent (no data, not charging). absent != still.
    secs = window_min * 60
    anchor = covered_buckets | charge_set
    start = (range_start or (min(anchor) if anchor else now))
    start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_utc = now.astimezone(timezone.utc)
    span_windows = max(0, int((end_utc - start).total_seconds() // secs))
    charge_in_range = {b for b in charge_set if start <= b < end_utc}
    worn_n = len(covered_buckets)                       # already excludes charging
    charge_n = len(charge_in_range)
    absent_n = max(0, span_windows - worn_n - charge_n)
    worn_min = worn_n * window_min
    charging_min = charge_n * window_min
    absent_min = absent_n * window_min
    coverage = min(1.0, worn_n / span_windows) if span_windows else 0.0

    # cross-tab: primary signal amount per activity (aligns by window start)
    by_activity: dict[str, float] = {}
    if primary:
        pdeltas = deltas[primary]
        for r in activity_rows:
            if not r.get("time"):
                continue
            ts = _parse(r["time"])
            bucket = datetime.fromtimestamp(
                (int(ts.timestamp()) // secs) * secs, timezone.utc)
            amt = pdeltas.get(bucket)
            if amt is None:
                continue
            state = _state(r)
            if state == UNKNOWN:
                continue
            by_activity[state] = by_activity.get(state, 0.0) + amt

    return BodySummary(
        person_id=person_id, signals=signals, primary=primary,
        units={s: _unit_for(s) for s in signals},
        total={k: round(v, 2) for k, v in total.items()},
        coverage=round(coverage, 4), worn_min=worn_min,
        charging_min=charging_min, absent_min=absent_min,
        active_min=active_min, sedentary_min=sedentary_min,
        per_day=[per_day[k] for k in sorted(per_day)],
        rhythm=[RhythmCell(dow=d, hour=h, totals=t) for (d, h), t in sorted(rhythm.items())],
        by_activity={k: round(v, 2) for k, v in by_activity.items()})


def active_sedentary_trends(counters: dict[str, list[tuple[datetime, float]]],
                            charging: list[tuple[datetime, object]] | None = None, *,
                            tz: str = "UTC", now: datetime | None = None,
                            window_min: int = 30, period_days: int = 7) -> list[TrendCallout]:
    """WoW callouts for ACTIVE and SEDENTARY worn-minutes/day (last `period_days`
    vs the prior `period_days`). Needs ~2*period_days of counter data."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    charge_set = _charge_buckets(charging or [], window_min)
    primary = _pick_primary([
        lbl for lbl, s in counters.items()
        if s and any(b not in charge_set for b in _deltas(s, window_min))])
    if primary is None:
        return []
    pdeltas = {b: v for b, v in _deltas(counters[primary], window_min).items()
               if b not in charge_set}
    recent_cut = now.timestamp() - period_days * 86400
    prior_cut = now.timestamp() - 2 * period_days * 86400
    r_act = r_sed = p_act = p_sed = 0.0
    for bucket, amt in pdeltas.items():
        ts = bucket.timestamp()
        active = amt >= ACTIVE_STEP_WINDOW
        if ts >= recent_cut:
            r_act += window_min if active else 0.0
            r_sed += 0.0 if active else window_min
        elif ts >= prior_cut:
            p_act += window_min if active else 0.0
            p_sed += 0.0 if active else window_min
    out = [make_callout("active", r_act, p_act, period_days, basis="inferred"),
           make_callout("sedentary", r_sed, p_sed, period_days, basis="inferred")]
    return [c for c in out if c is not None]


def _samples_from_col(wide, name) -> list:
    col = wide[name].dropna()
    out = []
    for ts, v in col.items():
        out.append((ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, v))
    return out


def _charging_samples(repo, tsdb, pid: str, start: datetime, end: datetime) -> list:
    """Raw on/off samples from a bound charging sensor (entity/name mentions
    'charg'), or [] when none is bound."""
    try:
        binds = [b for b in repo.bindings()
                 if b.enabled and b.person_id in (None, pid)
                 and "charg" in f"{b.entity_id} {b.name}".lower()]
    except Exception:
        binds = []
    if not binds or tsdb is None:
        return []
    try:
        wide = tsdb.read_raw(binds, start, end, freq="30m")
    except Exception:
        return []
    if wide is None or getattr(wide, "empty", True):
        return []
    for b in binds:
        if b.name in wide.columns:
            return _samples_from_col(wide, b.name)
    return []


def read_body(repo, tsdb, pid: str, start: datetime, end: datetime, *,
              tz: str = "UTC", activity_rows: list[dict] | None = None) -> BodySummary | None:
    """Read the person's cumulative counters (Role.STEPS) + charging sensor and
    aggregate. The display summary covers [start, end]; the active/sedentary TREND
    always uses ≥14d so it's independent of the view range. None when nothing bound."""
    from ..schemas import Role
    try:
        binds = [b for b in repo.bindings()
                 if b.role == Role.STEPS and b.enabled and b.person_id in (None, pid)]
    except Exception:
        binds = []
    if not binds or tsdb is None:
        return None
    lookback_start = min(start, end - timedelta(days=14))
    try:
        wide = tsdb.read_raw(binds, lookback_start, end, freq="30m")
    except Exception:
        return None
    if wide is None or getattr(wide, "empty", True):
        return None
    counters_full = {b.name: _samples_from_col(wide, b.name)
                     for b in binds if b.name in wide.columns}
    counters_full = {k: v for k, v in counters_full.items() if v}
    if not counters_full:
        return None
    charging_full = _charging_samples(repo, tsdb, pid, lookback_start, end)

    def _after(samples, cut):
        return [(ts, v) for ts, v in samples
                if (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)) >= cut]

    cstart = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    counters_disp = {k: _after(v, cstart) for k, v in counters_full.items()}
    counters_disp = {k: v for k, v in counters_disp.items() if v}
    charging_disp = _after(charging_full, cstart)

    summary = summarize_body(pid, counters_disp, activity_rows or [], tz=tz,
                             now=end, range_start=start, charging=charging_disp)
    summary.trends = active_sedentary_trends(counters_full, charging_full, tz=tz, now=end)
    return summary

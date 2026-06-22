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

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .summary import UNKNOWN, RhythmCell, _parse, _state

# A reset is only believed when the counter falls by more than this fraction of
# its previous value — guards against tiny non-monotonic jitter being mistaken
# for a midnight rollover.
RESET_DROP_FRAC = 0.5


class BodyDay(BaseModel):
    date: str
    totals: dict[str, float]       # signal label -> amount accrued that day
    covered_min: float             # minutes with sensor data (device worn)


class BodySummary(BaseModel):
    person_id: str
    signals: list[str]             # labels present (e.g. ["steps", "floors"])
    primary: str | None            # the headline signal (steps if present)
    units: dict[str, str]          # label -> unit ("steps", "km", "floors", "")
    total: dict[str, float]        # signal -> total over the range
    coverage: float                # worn windows / total windows in range (0..1)
    per_day: list[BodyDay]
    rhythm: list[RhythmCell]       # 24h x dow grid of the PRIMARY signal
    by_activity: dict[str, float]  # activity slug -> primary-signal amount during it


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
                   range_start: datetime | None = None) -> BodySummary:
    zone = ZoneInfo(tz) if tz else timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(zone)

    deltas = {label: _deltas(s, window_min) for label, s in counters.items() if s}
    deltas = {k: v for k, v in deltas.items() if v}
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
            covered_buckets.add(bucket)

    # covered minutes per day = distinct windows with ANY signal that day
    cov_by_day: dict[str, set[datetime]] = {}
    for b in covered_buckets:
        cov_by_day.setdefault(b.astimezone(zone).date().isoformat(), set()).add(b)
    for date, day in per_day.items():
        day.covered_min = len(cov_by_day.get(date, set())) * window_min

    # coverage over the whole range: worn windows / total windows
    secs = window_min * 60
    start = (range_start or (min(covered_buckets) if covered_buckets else now))
    start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    span_windows = max(1, int((now.astimezone(timezone.utc) - start).total_seconds() // secs))
    coverage = min(1.0, len(covered_buckets) / span_windows)

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
        coverage=round(coverage, 4),
        per_day=[per_day[k] for k in sorted(per_day)],
        rhythm=[RhythmCell(dow=d, hour=h, totals=t) for (d, h), t in sorted(rhythm.items())],
        by_activity={k: round(v, 2) for k, v in by_activity.items()})

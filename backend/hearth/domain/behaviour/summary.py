"""Behaviour aggregation — pure functions over the published prediction timeline.

Input: prediction rows as the store returns them (dicts with at least
`time` (iso), `smoothed`/`predicted`, `model_version`). We aggregate the SMOOTHED
state (what the home acted on); `model_version` carries the basis:
  fact-v0  → KNOWN (foundational fact: away/asleep)
  rules-v0 → inferred (cold-start rule)
  else     → inferred (model)
`unknown`/empty → unclassified (counted in coverage, never as an activity).

Everything is timezone-aware and quantised to `window_min`. No I/O here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev
from zoneinfo import ZoneInfo

from pydantic import BaseModel

FACT_PREFIX = "fact"
RULE_PREFIX = "rules"
UNKNOWN = "unknown"


class DaySummary(BaseModel):
    date: str                      # local YYYY-MM-DD
    totals: dict[str, float]       # activity -> minutes (excludes unknown)
    unknown_min: float
    fact_min: float                # minutes that were KNOWN (facts)
    inferred_min: float            # minutes from the model/rules


class Segment(BaseModel):
    start: str                     # local iso
    end: str
    activity: str
    basis: str                     # fact | model | rule | unknown


class RhythmCell(BaseModel):
    dow: int                       # 0=Mon .. 6=Sun (local)
    hour: int                      # 0..23 (local)
    totals: dict[str, float]       # activity -> minutes observed in this cell


class Transition(BaseModel):
    src: str                       # activity you were in
    dst: str                       # activity you moved to
    count: int                     # times this change was observed
    prob: float                    # count / all changes leaving `src` (0..1)


class Session(BaseModel):
    activity: str
    count: int                     # number of episodes
    mean_min: float                # average episode length
    median_min: float
    longest_min: float
    last_ts: str | None = None     # latest window of this activity (for drill-down)
    last_basis: str | None = None  # fact | model | rule (so the "why" is honest)


class Consistency(BaseModel):
    """Regularity of the trustworthy daily events (wake/bed), from sleep facts.
    Times are local minute-of-day; spread is the std-dev across nights, banded
    into plain language. None until there are at least 2 qualifying nights."""
    nights: int
    wake_avg_min: float | None = None
    wake_spread_min: float | None = None
    wake_band: str | None = None
    bed_avg_min: float | None = None
    bed_spread_min: float | None = None
    bed_band: str | None = None


class BehaviourSummary(BaseModel):
    person_id: str
    start: str
    end: str
    window_min: int
    totals: dict[str, float]       # activity -> minutes over the range
    total_min: float
    classified_min: float
    coverage: float                # classified / total (0..1)
    fact_min: float
    inferred_min: float
    known_fraction: float          # facts / classified (0..1)
    per_day: list[DaySummary]
    today: list[Segment]           # merged segments for the current local day
    sleep_per_day_min: dict[str, float]
    away_per_day_min: dict[str, float]
    rhythm: list[RhythmCell]       # 24h x day-of-week grid (when things happen)
    sequences: list[Transition]    # observed "what follows what" (self-loops excluded)
    sessions: list[Session]        # per-activity episode stats (count, length)
    consistency: Consistency       # wake/bed regularity from sleep facts


def _basis(model_version: str | None) -> str:
    v = (model_version or "").lower()
    if v.startswith(FACT_PREFIX):
        return "fact"
    if v.startswith(RULE_PREFIX):
        return "rule"
    return "model"


def _state(row: dict) -> str:
    s = row.get("smoothed") or row.get("predicted") or UNKNOWN
    return str(s) if s else UNKNOWN


def _parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def summarize(person_id: str, rows: list[dict], *, tz: str = "UTC",
              now: datetime | None = None, window_min: int = 30,
              sleep_slug: str = "asleep", away_slug: str = "away") -> BehaviourSummary:
    zone = ZoneInfo(tz) if tz else timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    w = float(window_min)

    days: dict[str, DaySummary] = {}
    totals: dict[str, float] = {}
    fact_min = inferred_min = unknown_total = 0.0
    sleep_day: dict[str, float] = {}
    away_day: dict[str, float] = {}
    today_local = now.date().isoformat()
    today_rows: list[tuple[datetime, str, str]] = []
    rhythm: dict[tuple[int, int], dict[str, float]] = {}
    trans: dict[tuple[str, str], int] = {}
    prev: tuple[datetime, str] | None = None
    seq_all: list[tuple[datetime, str]] = []
    last_seen: dict[str, tuple[str, str]] = {}   # activity -> (latest iso, basis)

    parsed = sorted(((_parse(r["time"]), r) for r in rows if r.get("time")),
                    key=lambda t: t[0])
    for ts, r in parsed:
        local = ts.astimezone(zone)
        date = local.date().isoformat()
        state = _state(r)
        basis = _basis(r.get("model_version"))
        seq_all.append((local, state))
        # rhythm grid + sequences (classified windows only)
        if state != UNKNOWN:
            cell = rhythm.setdefault((local.weekday(), local.hour), {})
            cell[state] = cell.get(state, 0.0) + w
            if prev is not None:
                pts, pstate = prev
                if (ts - pts).total_seconds() == w * 60 and pstate != state:
                    trans[(pstate, state)] = trans.get((pstate, state), 0) + 1
            prev = (ts, state)
        else:
            prev = None        # a gap/unknown breaks the consecutive chain
        day = days.get(date) or DaySummary(date=date, totals={}, unknown_min=0.0,
                                           fact_min=0.0, inferred_min=0.0)
        if state == UNKNOWN:
            day.unknown_min += w
            unknown_total += w
        else:
            day.totals[state] = day.totals.get(state, 0.0) + w
            totals[state] = totals.get(state, 0.0) + w
            if basis == "fact":
                day.fact_min += w; fact_min += w
            else:
                day.inferred_min += w; inferred_min += w
            last_seen[state] = (local.isoformat(), basis)
            if state == sleep_slug:
                sleep_day[date] = sleep_day.get(date, 0.0) + w
            elif state == away_slug:
                away_day[date] = away_day.get(date, 0.0) + w
        days[date] = day
        if date == today_local:
            today_rows.append((local, state, "unknown" if state == UNKNOWN else basis))

    total_min = sum(d.unknown_min + sum(d.totals.values()) for d in days.values())
    classified_min = total_min - unknown_total
    coverage = (classified_min / total_min) if total_min else 0.0
    known_fraction = (fact_min / classified_min) if classified_min else 0.0

    per_day = [days[k] for k in sorted(days)]
    start = per_day[0].date if per_day else today_local
    end = per_day[-1].date if per_day else today_local

    rhythm_cells = [RhythmCell(dow=d, hour=h, totals=t)
                    for (d, h), t in sorted(rhythm.items())]
    src_tot: dict[str, int] = {}
    for (a, _b), c in trans.items():
        src_tot[a] = src_tot.get(a, 0) + c
    sequences = sorted(
        (Transition(src=a, dst=b, count=c, prob=round(c / src_tot[a], 4))
         for (a, b), c in trans.items()),
        key=lambda x: (-x.count, x.src, x.dst))[:24]

    episodes = _episodes(seq_all, window_min)
    sessions = _sessions(episodes, last_seen)
    consistency = _consistency(episodes, sleep_slug)

    return BehaviourSummary(
        person_id=person_id, start=start, end=end, window_min=window_min,
        totals=totals, total_min=total_min, classified_min=classified_min,
        coverage=round(coverage, 4), fact_min=fact_min, inferred_min=inferred_min,
        known_fraction=round(known_fraction, 4), per_day=per_day,
        today=_segments(today_rows, window_min),
        sleep_per_day_min=sleep_day, away_per_day_min=away_day,
        rhythm=rhythm_cells, sequences=sequences,
        sessions=sessions, consistency=consistency)


def _episodes(seq: list[tuple[datetime, str]], window_min: int
              ) -> list[tuple[str, datetime, datetime]]:
    """Merge contiguous same-state windows into episodes (state, start, end).
    A time gap or a state change starts a new episode; unknown is kept (it breaks
    runs) and filtered by callers."""
    out: list[list] = []
    for local, state in seq:
        end = local + timedelta(minutes=window_min)
        if out and out[-1][0] == state and out[-1][2] == local:
            out[-1][2] = end
        else:
            out.append([state, local, end])
    return [(s, a, b) for s, a, b in out]


def _sessions(episodes: list[tuple[str, datetime, datetime]],
              last_seen: dict[str, tuple[str, str]] | None = None) -> list[Session]:
    last_seen = last_seen or {}
    by: dict[str, list[float]] = {}
    for state, start, end in episodes:
        if state == UNKNOWN:
            continue
        by.setdefault(state, []).append((end - start).total_seconds() / 60.0)
    out = []
    for a, ds in by.items():
        ts, basis = last_seen.get(a, (None, None))
        out.append(Session(activity=a, count=len(ds), mean_min=round(mean(ds), 1),
                           median_min=round(median(ds), 1), longest_min=round(max(ds), 1),
                           last_ts=ts, last_basis=basis))
    out.sort(key=lambda s: -s.count)
    return out


def _band(spread: float) -> str:
    if spread <= 20:
        return "very regular"
    if spread <= 45:
        return "fairly regular"
    if spread <= 90:
        return "varies"
    return "irregular"


def _consistency(episodes: list[tuple[str, datetime, datetime]],
                 sleep_slug: str, min_sleep_min: float = 180.0) -> Consistency:
    """Wake/bed regularity from night-sleep episodes (>= min_sleep_min). Bedtimes
    are measured as minutes since 18:00 so they don't wrap at midnight; wake times
    are plain minute-of-day (mornings don't wrap)."""
    beds, wakes = [], []
    for state, start, end in episodes:
        if state != sleep_slug or (end - start).total_seconds() / 60.0 < min_sleep_min:
            continue
        beds.append(((start.hour * 60 + start.minute) - 18 * 60) % 1440)
        wakes.append(end.hour * 60 + end.minute)
    nights = len(wakes)
    if nights < 2:
        return Consistency(nights=nights)
    wake_sd, bed_sd = pstdev(wakes), pstdev(beds)
    return Consistency(
        nights=nights,
        wake_avg_min=round(mean(wakes)), wake_spread_min=round(wake_sd), wake_band=_band(wake_sd),
        bed_avg_min=round((mean(beds) + 18 * 60) % 1440), bed_spread_min=round(bed_sd),
        bed_band=_band(bed_sd))


class TrendCallout(BaseModel):
    activity: str
    recent_avg_min: float          # avg minutes/day over the recent period
    prior_avg_min: float           # avg minutes/day over the period before that
    delta_min: float               # recent - prior (minutes/day)
    pct: float                     # delta / prior (0..; 1.0 when prior was 0)
    direction: str                 # up | down | new | stopped
    basis: str                     # fact | mixed | inferred (of the recent period)


def make_callout(activity: str, recent_total: float, prior_total: float,
                 period_days: int, *, basis: str = "inferred",
                 min_delta_min: float = 20.0, min_pct: float = 0.25) -> TrendCallout | None:
    """Turn a recent-vs-prior pair of TOTAL minutes into a notable callout, or None.
    Notable = a clean start/stop with a sizeable side, or a move clearing both an
    absolute (min_delta_min/day) and relative (min_pct) floor. Shared by activity
    trends and body (active/sedentary) trends."""
    r_avg = recent_total / period_days
    p_avg = prior_total / period_days
    delta = r_avg - p_avg
    if p_avg == 0 and r_avg == 0:
        return None
    if p_avg == 0:
        direction, pct = "new", 1.0
    elif r_avg == 0:
        direction, pct = "stopped", -1.0
    else:
        direction = "up" if delta > 0 else "down"
        pct = delta / p_avg
    sizeable = max(r_avg, p_avg) >= min_delta_min
    moved = abs(delta) >= min_delta_min and abs(pct) >= min_pct
    if not ((direction in ("new", "stopped") and sizeable) or moved):
        return None
    return TrendCallout(activity=activity, recent_avg_min=round(r_avg, 1),
                        prior_avg_min=round(p_avg, 1), delta_min=round(delta, 1),
                        pct=round(pct, 4), direction=direction, basis=basis)


def trends(person_id: str, rows: list[dict], *, tz: str = "UTC",
           now: datetime | None = None, window_min: int = 30, period_days: int = 7,
           min_delta_min: float = 20.0, min_pct: float = 0.25) -> list[TrendCallout]:
    """Week-over-week "what changed": average minutes/day per activity in the last
    `period_days` vs the `period_days` before that. Only NOTABLE changes are
    returned (|delta| >= min_delta_min AND |pct| >= min_pct, or a clean
    start/stop), so it's signal, not noise. Pure; needs ~2*period_days of rows."""
    zone = ZoneInfo(tz) if tz else timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    w = float(window_min)
    recent_cut = now.timestamp() - period_days * 86400
    prior_cut = now.timestamp() - 2 * period_days * 86400

    recent: dict[str, float] = {}
    prior: dict[str, float] = {}
    fact_recent: dict[str, float] = {}
    for r in rows:
        if not r.get("time"):
            continue
        state = _state(r)
        if state == UNKNOWN:
            continue
        ts = _parse(r["time"]).timestamp()
        if ts >= recent_cut:
            recent[state] = recent.get(state, 0.0) + w
            if _basis(r.get("model_version")) == "fact":
                fact_recent[state] = fact_recent.get(state, 0.0) + w
        elif ts >= prior_cut:
            prior[state] = prior.get(state, 0.0) + w

    out: list[TrendCallout] = []
    for act in set(recent) | set(prior):
        rtot = recent.get(act, 0.0)
        ftot = fact_recent.get(act, 0.0)
        frac = (ftot / rtot) if rtot else 0.0
        basis = "fact" if frac >= 0.8 else "inferred" if frac <= 0.05 else "mixed"
        c = make_callout(act, rtot, prior.get(act, 0.0), period_days, basis=basis,
                         min_delta_min=min_delta_min, min_pct=min_pct)
        if c is not None:
            out.append(c)
    out.sort(key=lambda t: -abs(t.delta_min))
    return out[:6]


def _segments(rows: list[tuple[datetime, str, str]], window_min: int) -> list[Segment]:
    """Merge consecutive same-activity windows into ribbon segments for today."""
    from datetime import timedelta
    out: list[Segment] = []
    for local, state, basis in rows:
        end = local + timedelta(minutes=window_min)
        if out and out[-1].activity == state and out[-1].basis == basis \
                and out[-1].end == local.isoformat():
            out[-1].end = end.isoformat()
        else:
            out.append(Segment(start=local.isoformat(), end=end.isoformat(),
                               activity=state, basis=basis))
    return out

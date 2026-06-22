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

from datetime import datetime, timezone
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

    parsed = sorted(((_parse(r["time"]), r) for r in rows if r.get("time")),
                    key=lambda t: t[0])
    for ts, r in parsed:
        local = ts.astimezone(zone)
        date = local.date().isoformat()
        state = _state(r)
        basis = _basis(r.get("model_version"))
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

    return BehaviourSummary(
        person_id=person_id, start=start, end=end, window_min=window_min,
        totals=totals, total_min=total_min, classified_min=classified_min,
        coverage=round(coverage, 4), fact_min=fact_min, inferred_min=inferred_min,
        known_fraction=round(known_fraction, 4), per_day=per_day,
        today=_segments(today_rows, window_min),
        sleep_per_day_min=sleep_day, away_per_day_min=away_day)


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

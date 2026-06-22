"""Behaviour API — habits & routines analytics over the prediction store.

bind(repo, tsdb) in main.py, then app.include_router(behaviour_routes.router).
GET /api/behaviour?person=&days= → a BehaviourSummary plus the person list and the
activity palette (slug→name→color) so the UI can render with consistent colours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from ..domain.behaviour.body import summarize_body
from ..domain.behaviour.summary import summarize, trends
from ..domain.schemas import Role

router = APIRouter(prefix="/api/behaviour", tags=["behaviour"])

_repo = None
_tsdb = None


def bind(repo, tsdb=None) -> None:
    global _repo, _tsdb
    _repo, _tsdb = repo, tsdb


@router.get("")
def behaviour(person: str | None = None, days: int = 7) -> dict:
    persons = []
    try:
        persons = [p for p in _repo.persons()] if _repo else []
    except Exception:
        persons = []
    plist = [{"id": p.id, "name": getattr(p, "name", p.id)} for p in persons]
    acts = []
    try:
        acts = [{"slug": a.slug, "name": a.name, "color": getattr(a, "color", "#888888")}
                for a in _repo.activities()] if _repo else []
    except Exception:
        acts = []

    pid = person or (persons[0].id if persons else None)
    if pid is None or _tsdb is None:
        return {"summary": None, "persons": plist, "activities": acts}

    days = max(1, min(int(days or 7), 92))
    tz = (_repo.get_setting("timezone", "UTC") or "UTC") if _repo else "UTC"
    end = datetime.now(timezone.utc)
    # Trends always compare the last 7d vs the prior 7d, independent of the view
    # toggle, so fetch at least 14d and scope the display aggregation to `days`.
    lookback = max(days, 14)
    start = end - timedelta(days=lookback)
    try:
        rows = _tsdb.read_predictions(pid, start, end)
    except Exception:
        rows = []
    disp_cut = (end - timedelta(days=days)).timestamp()
    disp_rows = [r for r in rows if _after(r, disp_cut)]
    s = summarize(pid, disp_rows, tz=tz)
    t = trends(pid, rows, tz=tz)
    body = _body(pid, disp_rows, end - timedelta(days=days), end, tz)
    return {"summary": s.model_dump(mode="json"),
            "trends": [c.model_dump(mode="json") for c in t],
            "body": body.model_dump(mode="json") if body else None,
            "persons": plist, "activities": acts}


def _body(pid: str, disp_rows: list, start, end, tz: str):
    """Read the person's cumulative counter sensors (Role.STEPS) and aggregate
    them into the body-activity band. Returns None when nothing is bound."""
    try:
        binds = [b for b in _repo.bindings()
                 if b.role == Role.STEPS and b.enabled
                 and b.person_id in (None, pid)]
    except Exception:
        binds = []
    if not binds or _tsdb is None:
        return None
    try:
        wide = _tsdb.read_raw(binds, start, end, freq="30m")
    except Exception:
        return None
    if wide is None or getattr(wide, "empty", True):
        return None
    counters: dict[str, list] = {}
    for b in binds:
        if b.name not in wide.columns:
            continue
        col = wide[b.name].dropna()
        counters[b.name] = [(ts.to_pydatetime(), float(v)) for ts, v in col.items()]
    if not any(counters.values()):
        return None
    charging = _charging_samples(pid, start, end)
    return summarize_body(pid, counters, disp_rows, tz=tz,
                          now=end, range_start=start, charging=charging)


def _charging_samples(pid: str, start, end) -> list:
    """Read a bound phone-charging sensor (binary charging state — any binding
    whose entity/name mentions 'charg'). Raw on/off samples; the aggregator
    interprets truthiness. Returns [] when none is bound."""
    try:
        binds = [b for b in _repo.bindings()
                 if b.enabled and b.person_id in (None, pid)
                 and "charg" in f"{b.entity_id} {b.name}".lower()]
    except Exception:
        binds = []
    if not binds or _tsdb is None:
        return []
    try:
        wide = _tsdb.read_raw(binds, start, end, freq="30m")
    except Exception:
        return []
    if wide is None or getattr(wide, "empty", True):
        return []
    for b in binds:
        if b.name in wide.columns:
            col = wide[b.name].dropna()
            return [(ts.to_pydatetime(), v) for ts, v in col.items()]
    return []


def _after(row: dict, cut_ts: float) -> bool:
    ts = row.get("time")
    if not ts:
        return False
    try:
        d = datetime.fromisoformat(ts)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp() >= cut_ts
    except Exception:
        return False

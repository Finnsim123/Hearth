"""Behaviour API — habits & routines analytics over the prediction store.

bind(repo, tsdb) in main.py, then app.include_router(behaviour_routes.router).
GET /api/behaviour?person=&days= → a BehaviourSummary plus the person list and the
activity palette (slug→name→color) so the UI can render with consistent colours.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from ..domain.behaviour.body import read_body
from ..domain.behaviour.summary import summarize, trends

router = APIRouter(prefix="/api/behaviour", tags=["behaviour"])

_repo = None
_tsdb = None

# Short-lived in-process cache for the heavy multi-day prediction reads this page
# makes (the personal 14-day pull + one read per household member). New
# predictions land every ~5 min, so a 60 s TTL is invisible to the user but turns
# repeat visits / the household N+1 into near-instant reads.
_PRED_TTL = 60.0
_pred_cache: dict = {}


def bind(repo, tsdb=None) -> None:
    global _repo, _tsdb
    _repo, _tsdb = repo, tsdb
    _pred_cache.clear()


def _read_predictions_cached(pid: str, start: datetime, end: datetime) -> list:
    """read_predictions with a 60 s TTL cache keyed by (person, hour-bucketed
    range). Bounded so it can't grow unbounded."""
    key = (pid, int(start.timestamp() // 3600), int(end.timestamp() // 3600))
    now = time.monotonic()
    hit = _pred_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    rows = _tsdb.read_predictions(pid, start, end)
    _pred_cache[key] = (now + _PRED_TTL, rows)
    if len(_pred_cache) > 64:                       # evict expired, keep it small
        for k in [k for k, (exp, _) in _pred_cache.items() if exp <= now]:
            _pred_cache.pop(k, None)
    return rows


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
        rows = _read_predictions_cached(pid, start, end)
    except Exception:
        rows = []
    disp_cut = (end - timedelta(days=days)).timestamp()
    disp_rows = [r for r in rows if _after(r, disp_cut)]
    s = summarize(pid, disp_rows, tz=tz)
    t = trends(pid, rows, tz=tz)
    try:
        body = read_body(_repo, _tsdb, pid, end - timedelta(days=days), end,
                         tz=tz, activity_rows=disp_rows)
    except Exception:
        body = None
    return {"summary": s.model_dump(mode="json"),
            "trends": [c.model_dump(mode="json") for c in t],
            "body": body.model_dump(mode="json") if body else None,
            "marker_flags": _marker_flags(pid, s.today),
            "persons": plist, "activities": acts}


def _marker_flags(pid: str, today) -> list[dict]:
    """Today's transition moments: where the published state crossed a marker's
    from→to boundary. Rendered as flags on the Today ribbon."""
    try:
        from ..domain.markers import markers_for
        mk = markers_for(_repo, pid) if _repo else []
    except Exception:
        mk = []
    if not mk:
        return []
    out = []
    for i in range(1, len(today)):
        prev, cur = today[i - 1].activity, today[i].activity
        for m in mk:
            if m.to_state == cur and m.from_state in (None, prev):
                out.append({"time": today[i].start, "name": m.name, "to": cur})
                break
    return out


@router.get("/share")
def get_share(person: str) -> dict:
    from ..domain.behaviour.household import shares
    return {"person": person, "shares": shares(_repo, person) if _repo else False}


@router.post("/share")
def set_share(body: dict) -> dict:
    from ..domain.behaviour.household import set_share as _set
    pid = body.get("person")
    enabled = bool(body.get("enabled"))
    if _repo is not None and pid:
        _set(_repo, pid, enabled)
    return {"person": pid, "shares": enabled}


@router.get("/household")
def household(person: str | None = None, days: int = 7) -> dict:
    from ..domain.behaviour.household import cooccurrence, opted_in_ids, shares
    if _repo is None or _tsdb is None:
        return {"enabled": False, "shared": [], "self_shared": False, "pairs": []}
    try:
        persons = list(_repo.persons())
    except Exception:
        persons = []
    name = {p.id: getattr(p, "name", p.id) for p in persons}
    opted = opted_in_ids(_repo)
    pid = person or (persons[0].id if persons else None)
    self_shared = bool(pid and shares(_repo, pid))
    # gate: the viewer must have opted in, and there must be someone to compare to
    if not pid or not self_shared or len(opted) < 2:
        return {"enabled": False, "shared": opted, "self_shared": self_shared, "pairs": []}

    days = max(1, min(int(days or 7), 92))
    tz = (_repo.get_setting("timezone", "UTC") or "UTC")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        a_rows = _read_predictions_cached(pid, start, end)
    except Exception:
        a_rows = []
    pairs = []
    for other in opted:
        if other == pid:
            continue
        try:
            b_rows = _read_predictions_cached(other, start, end)
        except Exception:
            b_rows = []
        items = cooccurrence(a_rows, b_rows)
        if items:
            pairs.append({"other_id": other, "other_name": name.get(other, other),
                          "items": [it.model_dump(mode="json") for it in items]})
    return {"enabled": True, "shared": opted, "self_shared": True,
            "self_name": name.get(pid, pid), "pairs": pairs}


@router.get("/digest")
def get_digest() -> dict:
    en = bool(_repo.get_setting("behaviour.digest.enabled")) if _repo else False
    return {"enabled": en}


@router.post("/digest")
def set_digest(body: dict) -> dict:
    enabled = bool(body.get("enabled"))
    if _repo is not None:
        _repo.set_setting("behaviour.digest.enabled", enabled)
    return {"enabled": enabled}


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

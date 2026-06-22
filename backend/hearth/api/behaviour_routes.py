"""Behaviour API — habits & routines analytics over the prediction store.

bind(repo, tsdb) in main.py, then app.include_router(behaviour_routes.router).
GET /api/behaviour?person=&days= → a BehaviourSummary plus the person list and the
activity palette (slug→name→color) so the UI can render with consistent colours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from ..domain.behaviour.summary import summarize

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
    start = end - timedelta(days=days)
    try:
        rows = _tsdb.read_predictions(pid, start, end)
    except Exception:
        rows = []
    s = summarize(pid, rows, tz=tz)
    return {"summary": s.model_dump(mode="json"), "persons": plist, "activities": acts}

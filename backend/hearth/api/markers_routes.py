"""Transition markers API — bind(repo) in main.py, then include_router.

GET  /api/markers        list markers + candidate sensors + activities (for selectors)
POST /api/markers        upsert a marker (keyed by slug)
POST /api/markers/delete remove one
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.markers import Marker, load_markers, save_markers

router = APIRouter(prefix="/api/markers", tags=["markers"])

_repo = None


def bind(repo) -> None:
    global _repo
    _repo = repo


@router.get("")
def list_markers() -> dict:
    markers = load_markers(_repo) if _repo else []
    binds, acts = [], []
    try:
        binds = [{"name": b.name, "entity_id": b.entity_id, "room": b.room}
                 for b in _repo.bindings() if getattr(b, "enabled", True)] if _repo else []
    except Exception:
        binds = []
    try:
        acts = [{"slug": a.slug, "name": a.name} for a in _repo.activities()] if _repo else []
    except Exception:
        acts = []
    return {"markers": [m.model_dump(mode="json") for m in markers],
            "bindings": binds, "activities": acts}


@router.post("")
def upsert_marker(body: dict) -> dict:
    if _repo is None:
        raise HTTPException(503, "no store")
    try:
        m = Marker(**body)
    except Exception as exc:
        raise HTTPException(400, f"invalid marker: {exc}")
    markers = [x for x in load_markers(_repo) if x.slug != m.slug]
    markers.append(m)
    save_markers(_repo, markers)
    return {"ok": True, "marker": m.model_dump(mode="json")}


@router.post("/delete")
def delete_marker(body: dict) -> dict:
    slug = (body or {}).get("slug")
    if _repo is not None and slug:
        save_markers(_repo, [m for m in load_markers(_repo) if m.slug != slug])
    return {"ok": True}

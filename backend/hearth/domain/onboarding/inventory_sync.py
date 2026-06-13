"""HA inventory sync — keep bindings in step with a changing Home Assistant.

Setup scans HA once. But homes evolve: you add a sensor, rename an entity, or
assign rooms to areas. This re-scans (daily on a schedule, or on demand) and
reconciles WITHOUT clobbering your manual edits:

  * AREA changed in HA      → the binding's room is updated.
  * NEW bindable entities   → STAGED for your approval (NOT auto-added). They
                              wait in `discovery.pending`; the buddy nudges you,
                              and only on approval are they bound + the model
                              retrained. This is the safety gate: adding a sensor
                              to test something must never silently burn tokens
                              or pull the sensor into training (gap analysis E4).
  * everything you touched  → left exactly as-is (roles, disables, overrides).

It never deletes a binding; the empty-sensor prune handles dead columns.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ..features.person_scope import binding_owner
from ..schemas import Binding, Role
from .advisor import heuristic_bindings
from .rooms import canonical_room, room_key

log = logging.getLogger(__name__)


def _slug(entity_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", entity_id.split(".", 1)[-1].lower()).strip("_") or "sensor"


async def sync_inventory(repo, events, use_llm: bool = False) -> dict:
    """Reconcile bindings with the live HA entity list. Updates rooms for bound
    entities and STAGES brand-new bindable entities for approval (does not add
    them). `use_llm` is accepted for signature compatibility but unused here —
    the LLM runs at APPROVAL time, not on every scan. Returns a summary."""
    try:
        inventory = await events.discover_entities()
    except Exception as exc:
        log.warning("inventory sync: discover failed: %s", exc)
        return {"error": str(exc), "pending": 0, "rooms_updated": 0}

    usable = [e for e in inventory if not e.get("disabled")]
    by_id = {e["entity_id"]: e for e in usable}
    existing = {b.entity_id: b for b in repo.bindings()}
    # refresh the known-areas list (drives the coverage map's blind-spot bubbles)
    repo.set_setting("ha.areas", sorted({e["area"] for e in usable if e.get("area")}))

    # 1. area/room updates for already-bound entities (safe; compare canonical key)
    rooms_updated = 0
    for eid, b in existing.items():
        area = canonical_room((by_id.get(eid) or {}).get("area"))
        if area and room_key(area) != room_key(b.room):
            b.room = area
            repo.save_binding(b)
            rooms_updated += 1

    # 2. brand-new bindable entities → stage for approval, do NOT bind
    fresh = [e for e in usable if e["entity_id"] not in existing]
    fresh_meta = {e["entity_id"]: e for e in fresh}
    suggested = heuristic_bindings(fresh)

    prev = repo.get_setting("discovery.pending") or []
    if not isinstance(prev, list):
        prev = []
    # keep prior pending entries that are still unseen-and-unbound; drop stale ones
    pending_by_id = {
        p["entity_id"]: p for p in prev
        if isinstance(p, dict) and p.get("entity_id") in by_id
        and p["entity_id"] not in existing
    }
    for b in suggested:
        meta = fresh_meta.get(b.entity_id, {})
        pending_by_id[b.entity_id] = {
            "entity_id": b.entity_id,
            "suggested_role": b.role.value,
            "suggested_name": b.name,
            "friendly_name": meta.get("friendly_name"),
            "area": canonical_room(meta.get("area")),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    pending = list(pending_by_id.values())
    repo.set_setting("discovery.pending", pending)

    # cache funnel stats for the Methodology page
    bound_ids = set(existing)
    leftover = [e["entity_id"] for e in usable if e["entity_id"] not in bound_ids]
    try:
        repo.set_setting("inventory.scan", {
            "entity_total": len(inventory), "usable": len(usable),
            "bindable_count": len(bound_ids), "filtered_examples": leftover[:6]})
    except Exception:
        log.debug("inventory sync: scan-stats cache failed", exc_info=True)

    if pending or rooms_updated:
        log.info("inventory sync: %d new sensors awaiting approval, %d rooms updated",
                 len(pending), rooms_updated)
    return {"pending": len(pending), "rooms_updated": rooms_updated,
            "seen": len(usable), "added": 0}


def approve_pending_sensors(repo, entity_ids: list[str] | None = None) -> int:
    """Approve staged sensors: create ENABLED bindings from their suggestions and
    remove them from `discovery.pending`. `entity_ids=None` approves all. Returns
    the count bound. (Heuristic binding here; the LLM re-analysis + background
    retrain are layered on at the API/orchestration level.)"""
    pending = repo.get_setting("discovery.pending") or []
    if not isinstance(pending, list):
        pending = []
    want = (set(entity_ids) if entity_ids
            else {p["entity_id"] for p in pending if isinstance(p, dict)})
    used_names = {b.name for b in repo.bindings()}
    persons = repo.persons()
    added, remaining = 0, []
    for p in pending:
        if not isinstance(p, dict) or p.get("entity_id") not in want:
            remaining.append(p)
            continue
        eid = p["entity_id"]
        try:
            role = Role(p.get("suggested_role"))
        except (ValueError, TypeError):
            remaining.append(p)
            continue
        name = p.get("suggested_name") or _slug(eid)
        base, n = name, 2
        while name in used_names:
            name, n = f"{base}_{n}", n + 1
        used_names.add(name)
        b = Binding(entity_id=eid, role=role, name=name,
                    room=canonical_room(p.get("area")), enabled=True)
        b.person_id = binding_owner(b, persons)
        try:
            repo.save_binding(b)
            added += 1
        except Exception:
            log.exception("approve: save_binding failed for %s", eid)
            remaining.append(p)
    repo.set_setting("discovery.pending", remaining)
    return added


def dismiss_pending_sensors(repo, entity_ids: list[str] | None = None) -> int:
    """Drop staged sensors without binding them. `entity_ids=None` dismisses all.
    Returns how many remain pending."""
    pending = repo.get_setting("discovery.pending") or []
    if not isinstance(pending, list):
        pending = []
    drop = set(entity_ids) if entity_ids else {p.get("entity_id") for p in pending
                                               if isinstance(p, dict)}
    kept = [p for p in pending if isinstance(p, dict) and p.get("entity_id") not in drop]
    repo.set_setting("discovery.pending", kept)
    return len(kept)

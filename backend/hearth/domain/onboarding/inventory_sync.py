"""HA inventory sync — keep bindings in step with a changing Home Assistant.

Setup scans HA once. But homes evolve: you add a sensor, rename an entity's
friendly name, or finally assign rooms to areas. This re-scans HA (daily on a
schedule, or on demand from the Sensors page) and reconciles WITHOUT clobbering
your manual edits:

  * NEW bindable entities  → added (heuristic role; LLM if a key is configured),
                             enabled so they start collecting immediately.
  * AREA changed in HA     → the binding's room is updated.
  * everything you touched  → left exactly as-is (roles, disables, overrides).

It never deletes: an entity that vanished may be a temporary blip, and the
empty-sensor prune already handles dead columns.
"""
from __future__ import annotations

import logging

from ..features.person_scope import binding_owner
from .advisor import heuristic_bindings

log = logging.getLogger(__name__)


async def sync_inventory(repo, events, use_llm: bool = False) -> dict:
    """Reconcile bindings with the live HA entity list. Returns a summary."""
    try:
        inventory = await events.discover_entities()
    except Exception as exc:
        log.warning("inventory sync: discover failed: %s", exc)
        return {"error": str(exc), "added": 0, "rooms_updated": 0}

    usable = [e for e in inventory if not e.get("disabled")]
    existing = {b.entity_id: b for b in repo.bindings()}
    used_names = {b.name for b in existing.values()}

    # 1. area/room updates for already-bound entities
    rooms_updated = 0
    by_id = {e["entity_id"]: e for e in usable}
    for eid, b in existing.items():
        area = (by_id.get(eid) or {}).get("area")
        if area and area != b.room:
            b.room = area
            repo.save_binding(b)
            rooms_updated += 1

    # 2. brand-new bindable entities
    fresh = [e for e in usable if e["entity_id"] not in existing]
    proposed = {b.entity_id: b for b in heuristic_bindings(fresh)}
    if use_llm and fresh and repo.get_connection("llm"):
        try:
            from ...adapters.openrouter_llm import OpenRouterAdvisor
            advisor = OpenRouterAdvisor(repo)
            for b in await advisor.propose_bindings(fresh, repo.persons()):
                proposed[b.entity_id] = b          # LLM wins ties
        except Exception:
            log.exception("inventory sync: LLM proposal failed — heuristics only")

    persons = repo.persons()
    added = 0
    for b in proposed.values():
        if not b.person_id:
            b.person_id = binding_owner(b, persons)
        base, n = b.name, 2                        # keep feature prefixes unique
        while b.name in used_names:
            b.name, n = f"{base}_{n}", n + 1
        used_names.add(b.name)
        try:
            repo.save_binding(b)
            added += 1
        except Exception:
            log.exception("inventory sync: save_binding failed for %s", b.entity_id)

    if added or rooms_updated:
        log.info("inventory sync: +%d new sensors, %d rooms updated", added, rooms_updated)
    return {"added": added, "rooms_updated": rooms_updated, "seen": len(usable)}

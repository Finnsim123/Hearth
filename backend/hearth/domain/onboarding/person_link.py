"""Linking household members to their home/away entity, reliably.

The away rule (and a lot of cold-start accuracy) depends on each member having
a person-role binding linked by person_id. The LLM does the messy-name → member
match; this module applies it, with a deterministic name-token fallback, and a
safety net that force-binds the core roles (person/presence/bed) the model most
relies on. Used by setup seeding and the manual 'relink' endpoint.
"""
from __future__ import annotations

import logging
import re

from ..schemas import Binding, Role

log = logging.getLogger(__name__)

CORE_ROLES = {Role.PERSON, Role.PRESENCE, Role.BED}
_PERSON_DOMAINS = ("person", "device_tracker")


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def fallback_match(members, inventory) -> dict[str, str]:
    """Name-token match member → person.*/device_tracker (person.* preferred)."""
    cands = [e for e in inventory
             if e["entity_id"].split(".")[0] in _PERSON_DOMAINS and not e.get("disabled")]
    cands.sort(key=lambda e: 0 if e["entity_id"].startswith("person.") else 1)
    out: dict[str, str] = {}
    for m in members:
        want = {m.id.lower()} | _tokens(m.name)
        for e in cands:
            hay = _tokens(e["entity_id"]) | _tokens(e.get("friendly_name") or "")
            if want & hay:
                out[m.id] = e["entity_id"]
                break
    return out


def ensure_member_persons(repo, inventory, llm_matches: dict | None = None) -> int:
    """Link every member lacking a person binding to a home/away entity:
    explicit (wizard) → LLM match → name fallback. Creates/links a person-role
    binding carrying person_id. Returns how many members got linked."""
    llm_matches = llm_matches or {}
    members = repo.persons()
    linked = {b.person_id for b in repo.bindings() if b.role == Role.PERSON and b.person_id}
    todo = [m for m in members if m.id not in linked]
    if not todo:
        return 0
    fb = fallback_match(todo, inventory)
    n = 0
    for m in todo:
        ent = getattr(m, "ha_person_entity", None) or llm_matches.get(m.id) or fb.get(m.id)
        if not ent:
            continue
        slug = (re.sub(r"[^a-z0-9]+", "_", m.id.lower()).strip("_") or m.id)
        existing = next((b for b in repo.bindings() if b.entity_id == ent), None)
        try:
            if existing is not None:
                existing.role, existing.person_id = Role.PERSON, m.id
                repo.save_binding(existing)
            else:
                repo.save_binding(Binding(entity_id=ent, role=Role.PERSON,
                                          name=f"{slug}_loc", person_id=m.id))
            n += 1
        except Exception:
            log.exception("link person failed for %s -> %s", m.id, ent)
    return n


def force_core_roles(repo, inventory) -> int:
    """Safety net: ensure the core roles (person/presence/bed) the model leans
    on are bound, even if the LLM pass dropped them. Returns # added."""
    from .advisor import heuristic_bindings
    bound = {b.entity_id for b in repo.bindings()}
    added = 0
    for b in heuristic_bindings(inventory):
        if b.role in CORE_ROLES and b.entity_id not in bound:
            try:
                repo.save_binding(b)
                added += 1
            except Exception:
                pass
    return added

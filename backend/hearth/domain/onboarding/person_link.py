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
from .advisor import is_person_tracker

log = logging.getLogger(__name__)

CORE_ROLES = {Role.PERSON, Role.PRESENCE, Role.BED}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def fallback_match(members, inventory) -> dict[str, str]:
    """Name-token match member → person.*/device_tracker (person.* preferred).
    Numeric distance/proximity entities are excluded — they are not trackers."""
    cands = [e for e in inventory
             if is_person_tracker(e["entity_id"], e.get("friendly_name") or "")
             and not e.get("disabled")]
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
        if not is_person_tracker(ent):
            # never promote a numeric proximity/distance entity to PERSON — that
            # is what produced the inverted "distance == 0 → away" rule.
            log.warning("skip person-link %s -> %s: not a home/away tracker", m.id, ent)
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


def repair_person_bindings(repo) -> dict:
    """Heal homes set up before the tracker guard existed: a numeric
    distance/proximity entity wrongly given the PERSON role produces a degenerate
    home_last (always 0) and the inverted 'home_last == 0 → away' starter rule.

    For each such binding: demote it to CUSTOM (so the distance signal still
    helps the model as a number, threshold learned) and clear person_id, then
    disable any rule whose predicate references that binding's *_home_last (the
    inverted away rule). Idempotent. Returns counts."""
    demoted, disabled = 0, 0
    bad_names: list[str] = []
    for b in repo.bindings():
        if b.role == Role.PERSON and not is_person_tracker(b.entity_id):
            bad_names.append(b.name)
            b.role, b.person_id = Role.CUSTOM, None
            try:
                repo.save_binding(b)
                demoted += 1
            except Exception:
                log.exception("repair: demote %s failed", b.entity_id)
    if bad_names:
        feats = {f"{n}_home_last" for n in bad_names}
        for r in repo.rules():
            if not r.enabled or r.id is None:
                continue
            if feats & _predicate_feats(r.predicate):
                r.enabled = False
                try:
                    repo.save_rule(r)
                    disabled += 1
                except Exception:
                    log.exception("repair: disable rule %s failed", r.id)
    return {"demoted": demoted, "rules_disabled": disabled}


def _predicate_feats(node) -> set[str]:
    """Every feature name referenced anywhere in a rule predicate AST."""
    if not isinstance(node, dict):
        return set()
    if "feat" in node:
        return {node["feat"]}
    out: set[str] = set()
    for v in node.values():
        if isinstance(v, list):
            for c in v:
                out |= _predicate_feats(c)
        elif isinstance(v, dict):
            out |= _predicate_feats(v)
    return out


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

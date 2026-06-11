"""Post-restart setup seeding — the slow half of onboarding.

setup_complete answers in milliseconds (account, connections, household,
taxonomy, fast-track marker) and restarts; THIS runs on the next boot:
inventory scan -> heuristic + LLM bindings -> member person_id wiring ->
template + LLM rules. Progress lands in settings("seed.status") so the
journey card can narrate it; the fast track only starts after seeding
(it imports the entities seeding just bound).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "member"


def _status(repo, stage: str, **extra) -> None:
    repo.set_setting("seed.status",
                     {"stage": stage, "at": datetime.now(timezone.utc).isoformat(), **extra})


async def run_seed(repo, events) -> None:
    """Idempotent: clears 'seed.pending' only on success; safe to re-run."""
    pending = repo.get_setting("seed.pending")
    if not pending:
        return
    from .advisor import heuristic_bindings
    from ..labeling.starter_rules import starter_rules
    from ..schemas import Binding, Role

    try:
        _status(repo, "scanning")
        inventory = await events.discover_entities()
        usable = [e for e in inventory if not e.get("disabled")]

        _status(repo, "mapping", entities=len(usable))
        advisor = None
        if repo.get_connection("llm"):
            from ...adapters.openrouter_llm import OpenRouterAdvisor
            advisor = OpenRouterAdvisor(repo)
        merged = {b.entity_id: b for b in heuristic_bindings(usable)}
        if advisor is not None:
            try:
                for b in await advisor.propose_bindings(usable, repo.persons()):
                    merged[b.entity_id] = b              # LLM wins ties
            except Exception:
                log.exception("LLM binding proposal failed — heuristics only")
        # ownership backfill: a member's name/id as a token in the binding
        # claims it ("nora_wekker" → nora) — person_scope uses this to
        # keep one member's alarm/phone out of another member's model
        from ..features.person_scope import binding_owner
        persons_now = repo.persons()
        for b in merged.values():
            if not b.person_id:
                b.person_id = binding_owner(b, persons_now)
            try:
                repo.save_binding(b)
            except Exception:
                pass

        # member person-entity bindings always carry person_id (update-or-create)
        from .advisor import is_person_tracker
        for m in pending.get("members", []):
            ent = m.get("personEntity")
            if not ent or not is_person_tracker(ent):
                continue
            existing = next((b for b in repo.bindings() if b.entity_id == ent), None)
            if existing is not None:
                existing.role = Role.PERSON
                existing.person_id = _slug(m["name"])
                repo.save_binding(existing)
            else:
                try:
                    repo.save_binding(Binding(entity_id=ent, role=Role.PERSON,
                                              name=f"{_slug(m['name'])}_loc",
                                              person_id=_slug(m["name"])))
                except Exception:
                    pass

        # Backstop linking: every member must have a person.* binding for the
        # away rule to work. The LLM handles messy names → the right member;
        # a name-token fallback covers no-key installs. Then guarantee core roles.
        from .person_link import (ensure_member_persons, force_core_roles,
                                   repair_person_bindings)
        llm_matches = {}
        if advisor is not None:
            try:
                llm_matches = await advisor.match_person_entities(repo.persons(), usable)
            except Exception:
                log.exception("LLM person match failed — name fallback only")
        linked = ensure_member_persons(repo, usable, llm_matches)
        force_core_roles(repo, usable)
        # heal any numeric distance/proximity entity that slipped into PERSON
        # before generating rules, so no inverted away rule is ever written.
        repair_person_bindings(repo)
        if linked:
            log.info("setup seeding: linked %d member(s) to a home/away entity", linked)

        _status(repo, "writing_rules", bindings=len(repo.bindings()))
        for rule in starter_rules(repo.bindings(), repo.activities()):
            repo.save_rule(rule)
        if advisor is not None:
            try:
                for rule in await advisor.propose_rules(repo.bindings(),
                                                        repo.activities()):
                    repo.save_rule(rule)
            except Exception:
                log.exception("LLM rule proposal failed — templates only")

        bound = repo.bindings()
        repo.set_setting("inventory.scan", {
            "entity_total": len(inventory), "usable": len(usable),
            "bindable_count": len(bound),
            "filtered_examples": [e["entity_id"] for e in usable
                                  if e["entity_id"] not in {b.entity_id for b in bound}][:6]})
        repo.set_setting("seed.pending", None)
        _status(repo, "done", bindings=len(repo.bindings()),
                rules=len(repo.rules()))
        log.info("setup seeding complete: %d bindings, %d rules",
                 len(repo.bindings()), len(repo.rules()))
    except Exception as exc:
        log.exception("setup seeding failed")
        _status(repo, "failed", error=str(exc))

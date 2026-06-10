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
        merged = {b.entity_id: b for b in heuristic_bindings(usable)}
        if repo.get_connection("llm"):
            try:
                from ...adapters.openrouter_llm import OpenRouterAdvisor
                advisor = OpenRouterAdvisor(repo)
                for b in await advisor.propose_bindings(usable):
                    merged[b.entity_id] = b              # LLM wins ties
            except Exception:
                log.exception("LLM binding proposal failed — heuristics only")
        for b in merged.values():
            try:
                repo.save_binding(b)
            except Exception:
                pass

        # member person-entity bindings always carry person_id (update-or-create)
        for m in pending.get("members", []):
            ent = m.get("personEntity")
            if not ent:
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

        _status(repo, "writing_rules", bindings=len(repo.bindings()))
        for rule in starter_rules(repo.bindings(), repo.activities()):
            repo.save_rule(rule)
        if repo.get_connection("llm"):
            try:
                from ...adapters.openrouter_llm import OpenRouterAdvisor
                advisor = OpenRouterAdvisor(repo)
                for rule in await advisor.propose_rules(repo.bindings(),
                                                        repo.activities()):
                    repo.save_rule(rule)
            except Exception:
                log.exception("LLM rule proposal failed — templates only")

        repo.set_setting("seed.pending", None)
        _status(repo, "done", bindings=len(repo.bindings()),
                rules=len(repo.rules()))
        log.info("setup seeding complete: %d bindings, %d rules",
                 len(repo.bindings()), len(repo.rules()))
    except Exception as exc:
        log.exception("setup seeding failed")
        _status(repo, "failed", error=str(exc))

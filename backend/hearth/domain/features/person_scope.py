"""Person-scoping of features — whose sensor is whose, and what transfers.

Problem (found live): Alex's model ranked *Nora's* alarm clock among its top
features. Personal-cadence sensors (alarm, phone focus, steps, battery) say
nothing about the OTHER person — at best they leak schedule correlation that
breaks the moment routines diverge.

Policy (no hardcoded names — ADR-anonymous):
  * Ownership comes from the binding's `person_id` when set (LLM / member
    matching), with a NAME-TOKEN FALLBACK: if a member's id or name appears as
    a token in the binding name or entity_id, that member owns it
    ("nora_wekker" → nora). Token = whole `_`/`.`-separated word, so
    "evie" never claims "movie_room".
  * PERSONAL_ROLES owned by another member are dropped from this person's
    feature matrix (training AND discovery; inference follows automatically
    because the estimator aligns to training columns).
  * Context roles (bed, presence, person-tracker, …) are NEVER dropped:
    the partner's bed side / home-state is genuine context for your model.
"""
from __future__ import annotations

import logging
import re

from ..schemas import Binding, Person, Role

log = logging.getLogger(__name__)

# Roles whose signal is meaningless for anyone but the owner.
PERSONAL_ROLES = {Role.ALARM_TIME, Role.FOCUS, Role.STEPS, Role.BATTERY}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t}


def binding_owner(binding: Binding, persons: list[Person]) -> str | None:
    """Explicit person_id wins; otherwise a member's id/name appearing as a
    token in the binding name or entity_id claims it. None = household-shared."""
    if binding.person_id:
        return binding.person_id
    hay = _tokens(binding.name) | _tokens(binding.entity_id)
    for p in persons:
        if {p.id.lower()} & hay or _tokens(p.name) & hay:
            return p.id
    return None


def foreign_personal_prefixes(bindings: list[Binding], persons: list[Person],
                              target_person: str) -> set[str]:
    """Binding names (= feature-column prefixes) that belong to ANOTHER
    member's personal-cadence sensor."""
    out = set()
    for b in bindings:
        if b.role not in PERSONAL_ROLES:
            continue
        owner = binding_owner(b, persons)
        if owner is not None and owner != target_person:
            out.add(b.name)
    return out


def drop_foreign_personal(feats, bindings: list[Binding], persons: list[Person],
                          target_person: str):
    """Remove other members' personal columns from a feature DataFrame.
    Returns (filtered_df, dropped_column_names)."""
    prefixes = foreign_personal_prefixes(bindings, persons, target_person)
    if not prefixes:
        return feats, []
    dropped = [c for c in feats.columns
               if any(c == p or c.startswith(p + "_") for p in prefixes)]
    if dropped:
        log.info("[%s] excluding %d foreign personal feature(s): %s",
                 target_person, len(dropped), ", ".join(sorted(dropped)[:8]))
    return feats.drop(columns=dropped), dropped

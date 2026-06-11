"""Room name reconciliation.

Home Assistant areas arrive with inconsistent casing and spelling, and a rescan
can flip a binding's room between variants ("Living_room" vs "livingroom"),
spawning duplicate rooms in the coverage view. Two layers fix this:

  * canonical_room / room_key — deterministic case + separator folding, applied
    wherever a room is set so variants stop being created.
  * tidy_rooms — merge the variants already in the DB; optional LLM pass folds
    SEMANTIC duplicates a string compare can't ("Sleepingroom" → "Bedroom").
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def room_key(name: str | None) -> str:
    """Lower-cased, alphanumeric-only merge key. 'Living_room', 'living room'
    and 'Livingroom' all collapse to 'livingroom'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def canonical_room(name: str | None) -> str | None:
    """Tidy display form: split on separators, Title-Case the words.
    'Living_room' -> 'Living Room', 'kitchen' -> 'Kitchen'. None stays None."""
    if not name:
        return None
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", name) if w]
    if not words:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _best_display(originals: list[str]) -> str:
    """Pick the most informative spelling (most word-boundaries, then longest)
    and canonicalise it."""
    rep = max(originals, key=lambda o: (len(re.split(r"[^a-zA-Z0-9]+", o)), len(o)))
    return canonical_room(rep) or rep


def tidy_rooms_deterministic(repo) -> dict:
    """Fold case/separator variants among existing bindings to one canonical
    display each. Returns {changed, rooms: [...canonical names...]}."""
    bindings = repo.bindings()
    groups: dict[str, list[str]] = {}
    for b in bindings:
        if b.room:
            groups.setdefault(room_key(b.room), []).append(b.room)
    canon = {k: _best_display(v) for k, v in groups.items()}
    changed = 0
    for b in bindings:
        if b.room:
            target = canon[room_key(b.room)]
            if target != b.room:
                b.room = target
                repo.save_binding(b)
                changed += 1
    return {"changed": changed, "rooms": sorted(set(canon.values()))}


def apply_room_mapping(repo, mapping: dict[str, str]) -> int:
    """Apply a {room -> canonical room} map (e.g. from the LLM) to bindings.
    Matched by canonical key so casing in the map doesn't matter."""
    by_key = {room_key(k): v for k, v in mapping.items()
              if isinstance(v, str) and v.strip()}
    changed = 0
    for b in repo.bindings():
        if not b.room:
            continue
        target = by_key.get(room_key(b.room))
        target = canonical_room(target) if target else None
        if target and target != b.room:
            b.room = target
            repo.save_binding(b)
            changed += 1
    return changed

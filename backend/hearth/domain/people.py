"""Person lifecycle — rename, disable, and 'remove & forget'.

A household member's identity is their stable `Person.id` (a slug minted once at
creation); the display name is just a label, so renaming is lossless and never
touches stored data. Membership changes, though, need real operations:

  * disable   — pause a member (keep everything). Reversible; it's the enabled
                flag, already honoured by ingest + trainer.
  * forget    — a genuine erasure for when someone leaves (moves out, divorce):
                purge everything that's THEIRS across the app DB and the
                time-series store, WITHOUT disturbing the rest of the household's
                (often years of) data, then refresh the people who stay so their
                models stop leaning on a person who's gone.

Shared sensors (a living-room motion sensor, no person_id) belong to the home and
are always kept; only the departing member's own sensors are removed.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _person_setting_keys(person_id: str) -> list[str]:
    """Settings entries scoped to a single person — cleared on forget."""
    return [f"transitions.{person_id}", f"durations.{person_id}",
            f"override.{person_id}", f"drift.{person_id}",
            f"capability.{person_id}", f"labels.suspects.{person_id}",
            f"selection.dropped.{person_id}"]


def forget_person(repo, tsdb, person_id: str, *, drop_bindings: bool = True) -> dict:
    """Erase a member. Returns {ok, name, counts, retrain:[person_id,…]} where
    `retrain` is the remaining enabled members the caller should refresh. Purely
    destructive + idempotent: a second call on an already-gone id is a no-op.

    Order matters: purge the time-series first (best-effort — a store hiccup must
    not strand half-deleted rows), then the app-DB cascade, then per-person
    settings, then a timeline event."""
    persons = repo.persons()
    target = next((p for p in persons if p.id == person_id), None)
    if target is None:
        return {"ok": False, "reason": "unknown_person"}
    name = target.name

    if tsdb is not None and hasattr(tsdb, "purge_person"):
        try:
            tsdb.purge_person(person_id)
        except Exception:
            log.exception("forget_person: time-series purge failed for %s", person_id)

    counts = repo.delete_person(person_id, drop_bindings=drop_bindings)

    for key in _person_setting_keys(person_id):
        try:
            repo.set_setting(key, None)
        except Exception:
            pass

    try:
        from . import events
        events.record_event(
            repo, "person_removed",
            f"Removed {name} and everything Hearth had learned about them",
            f"erased {counts.get('models', 0)} model(s), {counts.get('rules', 0)} rule(s), "
            f"{counts.get('bindings', 0)} sensor binding(s)")
    except Exception:
        log.debug("forget_person: event log failed", exc_info=True)

    remaining = [p.id for p in persons if p.id != person_id and p.enabled]
    return {"ok": True, "name": name, "counts": counts, "retrain": remaining}


def orphaned_identities(repo, tsdb) -> list[str]:
    """Person ids that HAVE stored history but no current person — the candidates
    to reclaim via relink (e.g. after a rename + reseed minted a fresh id and
    left the old one's data behind)."""
    if tsdb is None or not hasattr(tsdb, "person_ids_with_data"):
        return []
    try:
        had = tsdb.person_ids_with_data()
    except Exception:
        log.debug("orphaned_identities: store query failed", exc_info=True)
        return []
    live = {p.id for p in repo.persons()}
    return sorted(had - live)


def relink_person(repo, current_id: str, old_id: str) -> dict:
    """Re-key the person currently known as `current_id` onto a previous identity
    `old_id`, so orphaned history under `old_id` becomes theirs — no time-series
    rewrite, the series already carry `old_id`. Records a timeline event; the
    caller should retrain the (now re-keyed) person to pick up the reclaimed data."""
    res = repo.relink_person(current_id, old_id)
    if res.get("ok"):
        try:
            from . import events
            events.record_event(
                repo, "person_relinked",
                f"Reclaimed earlier data for {res.get('name') or old_id}",
                f"re-linked to the identity “{old_id}” "
                f"({res.get('counts', {}).get('bindings', 0)} sensor(s), "
                f"{res.get('counts', {}).get('rules', 0)} rule(s) re-pointed)")
        except Exception:
            log.debug("relink_person: event log failed", exc_info=True)
    return res

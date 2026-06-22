"""Event timeline — an append-only log of notable things Hearth did/decided, so the
system has a memory and the user gets insight into its life: a sensor was demoted, a
model was promoted, drift was detected, a blind spot appeared/cleared.

Settings-backed ring buffer (newest kept), pure. Producers call record_event at the
moment something meaningful happens; the Activity page reads list_events.
"""
from __future__ import annotations

from datetime import datetime, timezone

KEY = "system.events"
CAP = 120


def record_event(repo, kind: str, title: str, detail: str = "") -> None:
    try:
        evs = repo.get_setting(KEY)
        evs = evs if isinstance(evs, list) else []
        evs.append({"at": datetime.now(timezone.utc).isoformat(),
                    "kind": kind, "title": title, "detail": detail})
        repo.set_setting(KEY, evs[-CAP:])
    except Exception:
        pass


def list_events(repo, limit: int = 50) -> list[dict]:
    evs = repo.get_setting(KEY)
    evs = evs if isinstance(evs, list) else []
    return list(reversed(evs))[:limit]

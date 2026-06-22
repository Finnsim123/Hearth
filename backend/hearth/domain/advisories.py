"""Advisories — standing, dismissible recommendations the buddy can surface.

Distinct from `health` incidents (transient, auto-expiring): an advisory persists
until the producer CLEARS it (the thing it warned about resolved) or the user
DISMISSES it (snooze with a cooldown). Examples: a foundational sensor was demoted,
a room is a blind spot, the model's confidence is miscalibrated.

Settings-backed, pure (just reads/writes two settings):
  system.advisories            -> {kind: {kind,severity,title,detail,cta,at}}
  system.advisories.dismissed  -> {kind: iso_until}   (snoozed until this time)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KEY = "system.advisories"
DKEY = "system.advisories.dismissed"
_SEV = {"critical": 3, "warn": 2, "info": 1}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_advisory(repo, kind: str, title: str, detail: str, *,
                    severity: str = "warn", cta: dict | None = None) -> None:
    """Upsert an advisory. `at` (first-seen) is preserved across updates so the
    Activity log keeps the original time; dismissing it is cleared on a real change
    of title/detail so a materially new warning resurfaces."""
    advs = repo.get_setting(KEY) or {}
    prev = advs.get(kind)
    at = prev["at"] if prev else _now().isoformat()
    advs[kind] = {"kind": kind, "severity": severity, "title": title,
                  "detail": detail, "cta": cta, "at": at}
    repo.set_setting(KEY, advs)
    if prev and (prev.get("title") != title or prev.get("detail") != detail):
        _undismiss(repo, kind)        # a changed warning is worth showing again


def clear_advisory(repo, kind: str) -> None:
    advs = repo.get_setting(KEY) or {}
    if kind in advs:
        del advs[kind]
        repo.set_setting(KEY, advs)
    _undismiss(repo, kind)


def _undismiss(repo, kind: str) -> None:
    dis = repo.get_setting(DKEY) or {}
    if kind in dis:
        del dis[kind]
        repo.set_setting(DKEY, dis)


def dismiss_advisory(repo, kind: str, days: int = 14) -> None:
    dis = repo.get_setting(DKEY) or {}
    dis[kind] = (_now() + timedelta(days=days)).isoformat()
    repo.set_setting(DKEY, dis)


def _dismissed(repo, kind: str, now: datetime) -> bool:
    dis = repo.get_setting(DKEY) or {}
    until = dis.get(kind)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now
    except Exception:
        return False


def active_advisories(repo, *, include_dismissed: bool = False) -> list[dict]:
    """Active advisories, worst severity first then newest. Dismissed (snoozed)
    ones are excluded unless asked for."""
    advs = repo.get_setting(KEY) or {}
    now = _now()
    out = [a for k, a in advs.items()
           if include_dismissed or not _dismissed(repo, k, now)]
    # worst severity first, then newest first
    out.sort(key=lambda a: (_SEV.get(a.get("severity", "info"), 1), a.get("at", "")),
             reverse=True)
    return out


def worst_advisory(repo, *, min_severity: str = "warn") -> dict | None:
    """The single most important active advisory at/above `min_severity` — what the
    buddy bubble should surface. info-level advisories stay passive (Activity page)."""
    floor = _SEV.get(min_severity, 2)
    for a in active_advisories(repo):
        if _SEV.get(a.get("severity", "info"), 1) >= floor:
            return a
    return None

"""System incidents — so the buddy can say "hey, something's up".

Anything that genuinely breaks the pipeline (can't reach Home Assistant, history
fetch failing, …) records an issue here; the buddy resolver surfaces the most
recent one prominently. Issues self-expire after a few minutes so a hiccup that
recovered doesn't nag forever, and components clear their own issue the moment
things work again. Pure: just two settings reads/writes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

ISSUE_KEY = "system.issue"
DEFAULT_TTL_MIN = 10


def record_issue(repo, kind: str, title: str, detail: str, cta: dict | None = None) -> None:
    """Note a live problem (kind = a stable id so it can be cleared/deduped)."""
    try:
        repo.set_setting(ISSUE_KEY, {
            "kind": kind, "title": title, "detail": detail, "cta": cta,
            "at": datetime.now(timezone.utc).isoformat()})
        log.warning("issue recorded [%s]: %s — %s", kind, title, detail)
    except Exception:
        pass


def clear_issue(repo, kind: str | None = None) -> None:
    """Resolve the current issue (optionally only if it matches `kind`)."""
    try:
        cur = repo.get_setting(ISSUE_KEY)
        if cur and (kind is None or cur.get("kind") == kind):
            repo.set_setting(ISSUE_KEY, None)
    except Exception:
        pass


def current_issue(repo, ttl_min: int = DEFAULT_TTL_MIN) -> dict | None:
    """The active issue if it's recent enough to still matter, else None."""
    try:
        cur = repo.get_setting(ISSUE_KEY)
        if not cur:
            return None
        age = datetime.now(timezone.utc) - datetime.fromisoformat(cur["at"])
        if age.total_seconds() > ttl_min * 60:
            return None
        return cur
    except Exception:
        return None

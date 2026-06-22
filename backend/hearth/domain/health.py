"""System incidents — so the buddy can say "hey, something's up".

Anything that genuinely breaks the pipeline (can't reach Home Assistant, history
fetch failing, running heavy, …) records an issue here; the buddy surfaces the
WORST active one. Issues are keyed by `kind` so several can be live at once without
masking each other (an earlier single-slot design lost concurrent incidents), each
self-expires after a few minutes, and components clear their own the moment things
work again. Pure: settings reads/writes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

KEY = "system.issues"
LEGACY_KEY = "system.issue"          # pre-multi-issue single slot (read for migration)
DEFAULT_TTL_MIN = 10
_SEV = {"critical": 3, "warn": 2, "info": 1}


def _load(repo) -> dict:
    cur = repo.get_setting(KEY)
    if isinstance(cur, dict):
        return cur
    legacy = repo.get_setting(LEGACY_KEY)      # migrate a lingering single issue
    if isinstance(legacy, dict) and legacy.get("kind"):
        return {legacy["kind"]: legacy}
    return {}


def record_issue(repo, kind: str, title: str, detail: str,
                 cta: dict | None = None, severity: str = "critical") -> None:
    """Note a live problem. `kind` is a stable id (dedupes / lets it be cleared)."""
    try:
        issues = _load(repo)
        issues[kind] = {"kind": kind, "severity": severity, "title": title,
                        "detail": detail, "cta": cta,
                        "at": datetime.now(timezone.utc).isoformat()}
        repo.set_setting(KEY, issues)
        if repo.get_setting(LEGACY_KEY):
            repo.set_setting(LEGACY_KEY, None)
        log.warning("issue recorded [%s/%s]: %s — %s", severity, kind, title, detail)
    except Exception:
        pass


def clear_issue(repo, kind: str | None = None) -> None:
    """Resolve an issue by kind (or all if kind is None)."""
    try:
        issues = _load(repo)
        if kind is None:
            issues = {}
        elif kind in issues:
            del issues[kind]
        repo.set_setting(KEY, issues)
        if repo.get_setting(LEGACY_KEY):
            repo.set_setting(LEGACY_KEY, None)
    except Exception:
        pass


def active_issues(repo, ttl_min: int = DEFAULT_TTL_MIN) -> list[dict]:
    """Non-expired issues, worst severity first then newest."""
    try:
        issues = _load(repo)
        now = datetime.now(timezone.utc)
        live = []
        for it in issues.values():
            try:
                age = now - datetime.fromisoformat(it["at"])
            except Exception:
                continue
            if age.total_seconds() <= ttl_min * 60:
                live.append(it)
        live.sort(key=lambda it: (_SEV.get(it.get("severity", "critical"), 3),
                                  it.get("at", "")), reverse=True)
        return live
    except Exception:
        return []


def current_issue(repo, ttl_min: int = DEFAULT_TTL_MIN) -> dict | None:
    """The worst active issue (what the buddy surfaces), or None."""
    live = active_issues(repo, ttl_min)
    return live[0] if live else None

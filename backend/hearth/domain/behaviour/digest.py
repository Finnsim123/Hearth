"""Weekly buddy behaviour digest — a friendly, descriptive recap sent through the
existing HA notification channel (the same one milestones/asking use).

Descriptive, never judgemental; honest about certainty (leads with the trustworthy
facts — sleep/away — and footnotes how much of the week could be classified). Two
gates keep it consensual: a global opt-in setting `behaviour.digest.enabled` AND the
per-person system channel (`person.notify_system`), exactly like milestones.

compose_digest() is pure (testable); run_weekly_digest() does the I/O.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .body import read_body
from .summary import summarize, trends

log = logging.getLogger(__name__)


def _fmt_dur(minutes: float) -> str:
    m = int(round(minutes))
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h}h{mm}m"
    if h:
        return f"{h}h"
    return f"{mm}m"


def compose_digest(name: str, summary, trend_list, body, name_of: dict) -> tuple[str, str]:
    """Build (title, message) for one person's week. Pure."""
    nm = lambda slug: name_of.get(slug, slug)
    title = f"{name}'s week at home" if name else "Your week at home"
    parts: list[str] = []

    top = sorted(summary.totals.items(), key=lambda kv: -kv[1])[:3]
    if top:
        budget = ", ".join(f"{nm(slug)} {_fmt_dur(mins)}" for slug, mins in top)
        parts.append(f"Mostly: {budget}.")

    if summary.sleep_per_day_min:
        avg = sum(summary.sleep_per_day_min.values()) / 7
        parts.append(f"Slept about {_fmt_dur(avg)} a night.")
    if summary.away_per_day_min:
        avg = sum(summary.away_per_day_min.values()) / 7
        parts.append(f"Out of the house ~{_fmt_dur(avg)} a day.")

    changes = []
    for t in (trend_list or [])[:2]:
        label = nm(t.activity).lower()
        if t.direction in ("up", "down"):
            changes.append(f"{label} {t.direction} ~{_fmt_dur(abs(t.delta_min))}/day")
        elif t.direction == "new":
            changes.append(f"started {label}")
        else:
            changes.append(f"stopped {label}")
    if changes:
        parts.append("Changes: " + "; ".join(changes) + ".")

    if body and body.primary:
        if body.units.get(body.primary) == "steps":
            parts.append(f"Averaged {int(round(body.total.get(body.primary, 0.0) / 7)):,} steps a day.")
        if body.active_min:
            parts.append(f"Active about {_fmt_dur(body.active_min / 7)} a day.")
        for t in (body.trends or [])[:1]:
            lab = "Active" if t.activity == "active" else "Sedentary" if t.activity == "sedentary" else t.activity
            if t.direction in ("up", "down"):
                parts.append(f"{lab} time {t.direction} vs last week.")

    parts.append(f"(Based on the {round(summary.coverage * 100)}% of the week I could classify.)")
    return title, " ".join(parts)


def _within(row: dict, cutoff: datetime) -> bool:
    ts = row.get("time")
    if not ts:
        return False
    try:
        d = datetime.fromisoformat(ts)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d >= cutoff
    except Exception:
        return False


async def run_weekly_digest(repo, tsdb, notifier, *, now: datetime | None = None) -> int:
    """Compose + send each opted-in person's weekly behaviour digest. Returns the
    number sent. No-op unless `behaviour.digest.enabled` is set."""
    if not repo.get_setting("behaviour.digest.enabled"):
        return 0
    if tsdb is None or notifier is None:
        return 0
    now = now or datetime.now(timezone.utc)
    tz = repo.get_setting("timezone", "UTC") or "UTC"
    try:
        name_of = {a.slug: a.name for a in repo.activities()}
    except Exception:
        name_of = {}
    sent = 0
    for person in repo.persons():
        if not getattr(person, "enabled", True) or not getattr(person, "has_device", True):
            continue
        if not getattr(person, "notify_system", False):     # per-person opt-in
            continue
        try:
            rows = tsdb.read_predictions(person.id, now - timedelta(days=14), now)
        except Exception:
            rows = []
        disp = [r for r in rows if _within(r, now - timedelta(days=7))]
        s = summarize(person.id, disp, tz=tz, now=now)
        if s.total_min == 0:
            continue
        t = trends(person.id, rows, tz=tz, now=now)
        try:
            body = read_body(repo, tsdb, person.id, now - timedelta(days=7), now,
                             tz=tz, activity_rows=disp)
        except Exception:
            body = None
        title, message = compose_digest(getattr(person, "name", ""), s, t, body, name_of)
        try:
            if await notifier.notify(person, title, message):
                sent += 1
        except Exception:
            log.exception("behaviour digest failed for %s", person.id)
    repo.set_setting("behaviour.digest.last", now.isoformat())
    return sent

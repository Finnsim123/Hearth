"""Asking policy — ε-greedy uncertainty sampling with budgets and manners.

ask if confidence < threshold (uncertainty) OR with probability ε (exploration
— without it, confidently-wrong predictions are never corrected). Subject to:
daily budget, cooldown, same-label repeat suppression, quiet hours,
person.has_device. Questions always land in the Inbox; the notification is
just the push channel.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..schemas import Person, Prediction, Question

log = logging.getLogger(__name__)

ASK_THRESHOLD = 0.75
MARGIN_THRESHOLD = 0.25  # top-2 gap: cooking 55% vs eating 43% is a GREAT
                         # question even though 0.55 > naive uncertainty alone
EPSILON = 0.07
COOLDOWN_MIN = 30
REPEAT_MIN = 90

# Fallback when the activity has no explicit `silent` flag (old DBs, LLM
# taxonomies in other languages): slugs that obviously mean "asleep".
_SLEEP_WORDS = ("sleep", "slaap", "schlaf", "nap", "dormi", "sommeil", "sueno", "sueño")


def _is_sleep_like(slug: str) -> bool:
    s = slug.lower()
    return any(w in s for w in _SLEEP_WORDS)


def _is_silent(pred_slug: str, repo) -> bool:
    """True = never push about this prediction. You can't answer "are you
    asleep?" while asleep — the question goes to the Inbox instead, so the
    label can still be confirmed next morning."""
    try:
        for a in repo.activities():
            if a.slug == pred_slug:
                return bool(getattr(a, "silent", False)) or _is_sleep_like(a.slug)
    except Exception:
        pass
    return _is_sleep_like(pred_slug)


def _in_quiet_hours(person: Person, now: datetime, tz: str) -> bool:
    h = now.astimezone(ZoneInfo(tz)).hour
    start, end = person.quiet_hours
    return (start <= h or h < end) if start > end else (start <= h < end)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def maybe_ask(pred: Prediction, person: Person, repo, notifier) -> Question | None:
    now = _utcnow()
    tz = repo.get_setting("timezone", "UTC") or "UTC"

    # margin sampling (active-learning standard): ask when the top two
    # classes are CLOSE, not only when the winner is weak
    ranked_p = sorted(pred.probabilities.values(), reverse=True)
    margin = (ranked_p[0] - ranked_p[1]) if len(ranked_p) > 1 else 1.0
    uncertain = pred.confidence < ASK_THRESHOLD or margin < MARGIN_THRESHOLD
    explore = random.random() < EPSILON
    if not (uncertain or explore):
        return None
    if _in_quiet_hours(person, now, tz):
        return None
    today = repo.questions_since(person.id, now - timedelta(days=1))
    if today >= person.ask_budget_per_day:
        return None
    last = repo.last_question(person.id)
    if last and last.created_at:
        last_at = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=timezone.utc)
        age_min = (now - last_at).total_seconds() / 60
        if age_min < COOLDOWN_MIN:
            return None
        if last.predicted == pred.predicted and age_min < REPEAT_MIN:
            return None

    # Never push "are you sleeping?" — if they are, they can't answer; if the
    # push wakes them up, we've done harm. The question still lands in the
    # Inbox so the window can be confirmed next morning.
    silent = _is_silent(pred.predicted, repo)

    ranked = sorted(pred.probabilities.items(), key=lambda kv: -kv[1])
    alternatives = [s for s, _ in ranked[:3]] or [pred.predicted]
    q = Question(person_id=person.id, window_ts=pred.window_ts,
                 predicted=pred.predicted, confidence=pred.confidence,
                 alternatives=alternatives, probabilities=pred.probabilities,
                 channel="notification" if (person.has_device and not silent) else "inbox")
    q = repo.save_question(q)
    if silent:
        log.info("silent activity %s — question for %s goes to inbox only",
                 pred.predicted, person.id)
        return q
    if person.has_device and notifier is not None:
        try:
            await notifier.ask(q, person)
        except Exception:
            log.exception("notification ask failed (question stays in inbox)")
    log.info("asked %s about %s (%.0f%%, %s)", person.id, pred.predicted,
             pred.confidence * 100, "explore" if not uncertain else "uncertain")
    return q


def expire_stale_questions(repo, max_age_hours: int = 12) -> int:
    return repo.expire_questions(datetime.now(timezone.utc) - timedelta(hours=max_age_hours))

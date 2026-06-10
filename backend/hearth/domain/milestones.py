"""Milestone notifications — close the onboarding loop ("come back in 3 days").

The wizard's last screen promises Hearth will ping when something happens.
This service keeps that promise. Each milestone fires ONCE (flag in settings),
to every member with a device:

  recording_started   first raw events landed (sanity "it works" ping, ~1 h in)
  patterns_found      first cluster cards exist            (Phase 4 emits)
  model_live          first promoted model wrote a prediction  ← the big one
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

_MESSAGES = {
    "recording_started": ("Hearth is recording 🎉",
                          "Your sensors are flowing. Live your normal life — "
                          "I'll message you when I've learned something."),
    "patterns_found": ("Hearth found its first patterns",
                       "I spotted some recurring patterns in your home. "
                       "Come name them when you have a minute."),
    "model_live": ("Hearth is live ✨",
                   "The first model is trained and predictions are flowing "
                   "into Home Assistant. Check the dashboard!"),
}


async def check_milestones(repo, tsdb, notifier) -> None:
    """Scheduler job (every 30 min). Cheap checks, each guarded by a flag."""
    now = datetime.now(timezone.utc)

    async def fire(key: str) -> None:
        title, message = _MESSAGES[key]
        for person in repo.persons():
            await notifier.notify(person, title, message)
        repo.set_setting(f"milestone.{key}", now.isoformat())
        log.info("milestone fired: %s", key)

    if not repo.get_setting("milestone.recording_started"):
        if tsdb.count_raw_events(hours=2) > 50:
            await fire("recording_started")

    if not repo.get_setting("milestone.patterns_found"):
        if repo.clusters(status="new"):
            await fire("patterns_found")

    if not repo.get_setting("milestone.model_live"):
        for person in repo.persons():
            if person.enabled and tsdb.read_predictions(
                    person.id, now - timedelta(days=1), now):
                await fire("model_live")
                break

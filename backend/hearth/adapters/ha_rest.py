"""Notifier adapter — HA REST API.

notify(): plain milestone/status notifications.
ask():    dynamic actionable questions (phrasing engine). Action ids carry the
          question id + option index — HEARTH_<qid>_<idx> — because iOS never
          returns the notification tag (ADR-6). The Hearth HA INTEGRATION
          listens for these on HA's event bus and forwards them to
          POST /api/feedback/action — zero automations, zero YAML.
          Every notification also deep-links to /inbox?q=<qid>.
"""
from __future__ import annotations

import logging
import time

import aiohttp

from ..domain.labeling.phrasing import (button_titles, followup_message,
                                         option_universe, root_options)
from ..domain.schemas import Person, Question

log = logging.getLogger(__name__)

MUTE_SETTING = "notify.mute_entity"     # HA entity id; state "on" = mute pushes
_MUTE_CACHE_S = 60.0                    # don't hammer HA before every push


async def ha_entity_state(repo, entity_id: str) -> str | None:
    """Read one entity's current state via HA's REST API; None on any failure."""
    conn = repo.get_connection("ha")
    if conn is None or not entity_id:
        return None
    url = f"{conn['url'].rstrip('/')}/api/states/{entity_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url,
                                   headers={"Authorization": f"Bearer {conn['token']}"},
                                   timeout=aiohttp.ClientTimeout(10)) as r:
                r.raise_for_status()
                return str((await r.json()).get("state"))
    except Exception as exc:
        log.debug("state read for %s failed: %s", entity_id, exc)
        return None


class HaRestNotifier:
    """Implements domain.ports.Notifier."""

    def __init__(self, repo, base_url: str = "") -> None:
        self.repo = repo
        self.base_url = base_url  # Hearth's own URL for deep links (settings)
        self._mute_at = 0.0       # monotonic ts of the last mute check
        self._mute_val = False

    async def _muted(self) -> bool:
        """True while the configured mute entity (vacation mode, guest mode, …)
        reads 'on'. FAIL OPEN: unreadable/missing entity → not muted — a missed
        push is annoying; silently-dead notifications break trust. Cached 60 s.
        Only phone pushes are muted; fire_event (automations) never is."""
        entity = self.repo.get_setting(MUTE_SETTING) or ""
        if not entity:
            return False
        now = time.monotonic()
        if now - self._mute_at < _MUTE_CACHE_S:
            return self._mute_val
        state = await ha_entity_state(self.repo, entity)
        self._mute_at = now
        self._mute_val = (state or "").lower() == "on"
        return self._mute_val

    async def _post_notify(self, service: str, payload: dict) -> bool:
        if await self._muted():
            log.info("push suppressed — mute entity is on (%s)",
                     self.repo.get_setting(MUTE_SETTING))
            return False
        conn = self.repo.get_connection("ha")
        if conn is None:
            return False
        url = f"{conn['url'].rstrip('/')}/api/services/notify/{service}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers={"Authorization": f"Bearer {conn['token']}"},
                                        timeout=aiohttp.ClientTimeout(10)) as r:
                    r.raise_for_status()
            return True
        except Exception as exc:
            log.warning("notify %s failed: %s", service, exc)
            return False

    async def fire_event(self, event_type: str, data: dict) -> bool:
        """Fire a custom event on HA's bus — automations trigger on it
        instantly (platform: event). Used by the realtime inference lane."""
        conn = self.repo.get_connection("ha")
        if conn is None:
            return False
        url = f"{conn['url'].rstrip('/')}/api/events/{event_type}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data,
                                        headers={"Authorization": f"Bearer {conn['token']}"},
                                        timeout=aiohttp.ClientTimeout(10)) as r:
                    r.raise_for_status()
            return True
        except Exception as exc:
            log.warning("fire_event %s failed: %s", event_type, exc)
            return False

    async def notify(self, person: Person, title: str, message: str,
                     data: dict | None = None) -> bool:
        if not person.has_device or not person.notify_service:
            return False
        payload: dict = {"title": title, "message": message}
        if data:
            payload["data"] = data
        return await self._post_notify(person.notify_service, payload)

    async def ask(self, question: Question, person: Person) -> bool:
        """Send one dynamically-phrased actionable question. The question must
        already be saved (id + alternatives set by the asking policy)."""
        if not person.has_device or not person.notify_service or question.id is None:
            return False
        activities = self.repo.activities()
        probs = question.probabilities or {question.predicted: question.confidence}
        # message: a follow-up reads "was it one of these instead?"; a root
        # question reads in its uncertainty mode (confident / toss-up / unsure).
        if question.parent_id:
            message = followup_message(question.window_ts)
        else:
            message, _opts, _more = root_options(probs, activities, question.window_ts)
        titles = button_titles(question.alternatives, activities)

        # Confident single-option question = Yes / No. Otherwise list the options
        # and add "Other". The escape (No/Other) opens the next batch; only show
        # it while candidates remain unshown.
        confident = len(question.alternatives) == 1
        if confident:
            actions = [{"action": f"HEARTH_{question.id}_0", "title": "✓ Yes"}]
            escape_title = "No"
        else:
            actions = [{"action": f"HEARTH_{question.id}_{i}", "title": t}
                       for i, t in enumerate(titles)]
            escape_title = "Other"
        remaining = [s for s in option_universe(probs, activities)
                     if s not in set(question.asked)]
        if remaining:
            actions.append({"action": f"HEARTH_{question.id}_more", "title": escape_title})
        actions = actions[:3]

        base = self.base_url or self.repo.get_setting("hearth_base_url", "") or ""
        deep_link = f"{base.rstrip('/')}/inbox?q={question.id}"
        payload = {
            "title": "What are you up to?",
            "message": message,
            "data": {"actions": actions, "url": deep_link,
                     "tag": f"hearth_q_{question.id}", "persistent": False},
        }
        return await self._post_notify(person.notify_service, payload)

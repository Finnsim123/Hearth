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

import aiohttp

from ..domain.labeling.phrasing import button_titles, phrase_question
from ..domain.schemas import Person, Question

log = logging.getLogger(__name__)


class HaRestNotifier:
    """Implements domain.ports.Notifier."""

    def __init__(self, repo, base_url: str = "") -> None:
        self.repo = repo
        self.base_url = base_url  # Hearth's own URL for deep links (settings)

    async def _post_notify(self, service: str, payload: dict) -> bool:
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
        message, _ = phrase_question(probs, activities, question.window_ts)
        titles = button_titles(question.alternatives, activities)
        actions = [{"action": f"HEARTH_{question.id}_{i}",
                    "title": ("✓ " + t if i == 0 else t)}
                   for i, t in enumerate(titles[:3])]
        base = self.base_url or self.repo.get_setting("hearth_base_url", "") or ""
        deep_link = f"{base.rstrip('/')}/inbox?q={question.id}"
        payload = {
            "title": "What are you up to?",
            "message": message,
            "data": {"actions": actions, "url": deep_link,
                     "tag": f"hearth_q_{question.id}", "persistent": False},
        }
        return await self._post_notify(person.notify_service, payload)

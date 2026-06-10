"""Notifier adapter (+ EntityPublisher fallback) — HA REST API.

Notifications (ADR-6): action ids carry the question id —
HEARTH_<qid>_CONFIRM / _ALT1 / _ALT2 — because iOS does NOT return the
notification tag (home-assistant/iOS#1666; cost the prototype its entire
feedback loop). A shipped automation blueprint (deploy/ha/hearth_actions.yaml)
forwards every HEARTH_* action event to POST /api/feedback/action; all parsing
happens server-side. Every notification also includes a deep-link URI to
/inbox?q=<qid> as the always-works path.
"""
from __future__ import annotations


class HaRestNotifier:
    """Implements domain.ports.Notifier."""

    def __init__(self, repo) -> None:  # AppRepo
        raise NotImplementedError

    def ask(self, question) -> bool:
        """Build buttons: confirm + 2 most-confusable alternatives (from the
        model's probability vector, not a hardcoded map)."""
        raise NotImplementedError

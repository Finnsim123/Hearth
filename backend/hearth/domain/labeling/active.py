"""Asking policy — decides when a prediction becomes a question.

ε-greedy uncertainty sampling (lesson #4 from the prototype: uncertainty-only
never corrects confident mistakes):

    ask if confidence < threshold            (uncertainty)
    OR with probability ε on any window      (exploration, default 0.07)

subject to: per-person daily budget, cooldown, same-label repeat suppression,
quiet hours, person.has_device (kids: inbox-only, never notified), and the
person's HA-exposed opt-out switch.
"""
from __future__ import annotations

from ..ports import AppRepo, Notifier
from ..schemas import Person, Prediction, Question


def maybe_ask(
    pred: Prediction,
    person: Person,
    repo: AppRepo,
    notifier: Notifier,
) -> Question | None:
    """Create a Question (inbox always; notification if eligible). Returns it,
    or None if suppressed. Suppression reasons are logged for the UI."""
    raise NotImplementedError


def expire_stale_questions(repo: AppRepo, max_age_hours: int = 12) -> int:
    """Open questions older than max_age expire (answers to ancient windows
    are noise). Returns count expired."""
    raise NotImplementedError

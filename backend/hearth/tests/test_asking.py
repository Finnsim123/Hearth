"""Asking policy — budgets, cooldowns, quiet hours, ε-exploration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hearth.domain.labeling import active
from hearth.domain.schemas import Person, Prediction, Question


class AskRepo:
    def __init__(self):
        self.saved: list[Question] = []
        self._last: Question | None = None
        self.today = 0

    def get_setting(self, k, d=None): return "UTC" if k == "timezone" else d
    def questions_since(self, person, since): return self.today
    def last_question(self, person): return self._last
    def save_question(self, q):
        q.id = len(self.saved) + 1
        self.saved.append(q)
        return q


class SpyNotifier:
    def __init__(self): self.asked = []
    async def ask(self, q, person): self.asked.append(q); return True


def _pred(conf=0.5, ts_hour=14):
    return Prediction(person_id="alice", model_version="v",
                      window_ts=datetime(2026, 6, 1, ts_hour, 0, tzinfo=timezone.utc),
                      predicted="cooking", confidence=conf,
                      probabilities={"cooking": conf, "home": 1 - conf - 0.05, "movie": 0.05})


def _person(**kw):
    return Person(id="alice", name="Alice", notify_service="mobile_app_x",
                  quiet_hours=(23, 6), **kw)


@pytest.mark.asyncio
async def test_uncertain_prediction_asks_with_top3_buttons(monkeypatch):
    repo, notifier = AskRepo(), SpyNotifier()
    monkeypatch.setattr(active.random, "random", lambda: 0.99)  # no exploration
    q = await active.maybe_ask(_pred(0.5), _person(), repo, notifier)
    assert q is not None and q.alternatives[0] == "cooking" and len(q.alternatives) == 3
    assert notifier.asked and q.probabilities["cooking"] == 0.5


@pytest.mark.asyncio
async def test_confident_skipped_unless_exploring(monkeypatch):
    repo, notifier = AskRepo(), SpyNotifier()
    monkeypatch.setattr(active.random, "random", lambda: 0.99)
    assert await active.maybe_ask(_pred(0.95), _person(), repo, notifier) is None
    monkeypatch.setattr(active.random, "random", lambda: 0.01)  # ε fires
    assert await active.maybe_ask(_pred(0.95), _person(), repo, notifier) is not None


@pytest.mark.asyncio
async def test_budget_quiet_hours_cooldown(monkeypatch):
    monkeypatch.setattr(active.random, "random", lambda: 0.99)
    repo, notifier = AskRepo(), SpyNotifier()
    repo.today = 8                                              # budget spent
    assert await active.maybe_ask(_pred(0.4), _person(), repo, notifier) is None
    repo.today = 0
    monkeypatch.setattr(active, "_utcnow",
                        lambda: datetime(2026, 6, 1, 2, 0, tzinfo=timezone.utc))
    assert await active.maybe_ask(_pred(0.4, ts_hour=2), _person(), repo, notifier) is None  # quiet
    monkeypatch.setattr(active, "_utcnow",
                        lambda: datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc))
    repo._last = Question(person_id="alice", window_ts=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc),
                          predicted="cooking", confidence=0.5,
                          created_at=datetime(2026, 6, 1, 13, 55, tzinfo=timezone.utc))
    assert await active.maybe_ask(_pred(0.4), _person(), repo, notifier) is None  # cooldown


@pytest.mark.asyncio
async def test_no_device_lands_in_inbox_only(monkeypatch):
    monkeypatch.setattr(active.random, "random", lambda: 0.99)
    repo, notifier = AskRepo(), SpyNotifier()
    kid = Person(id="kid", name="Kid", has_device=False)
    q = await active.maybe_ask(_pred(0.4), kid, repo, notifier)
    assert q is not None and q.channel == "inbox"
    assert notifier.asked == []                                 # never notified


@pytest.mark.asyncio
async def test_sleep_prediction_never_pushes(monkeypatch):
    """You can't answer "are you asleep?" while asleep — and the push itself
    could wake you. Sleep-like predictions go to the Inbox only."""
    repo, notifier = AskRepo(), SpyNotifier()
    monkeypatch.setattr(active.random, "random", lambda: 0.99)
    pred = _pred(0.5)
    pred.predicted = "sleeping"
    pred.probabilities = {"sleeping": 0.5, "home": 0.45, "movie": 0.05}
    q = await active.maybe_ask(pred, _person(), repo, notifier)
    assert q is not None and q.channel == "inbox"   # label still confirmable
    assert notifier.asked == []                     # but NO push went out


@pytest.mark.asyncio
async def test_custom_silent_activity_respected(monkeypatch):
    """Households can mark any activity silent (e.g. "meditating")."""
    from hearth.domain.schemas import Activity
    repo, notifier = AskRepo(), SpyNotifier()
    repo.activities = lambda: [Activity(slug="meditating", name="Meditating", silent=True)]
    monkeypatch.setattr(active.random, "random", lambda: 0.99)
    pred = _pred(0.5)
    pred.predicted = "meditating"
    q = await active.maybe_ask(pred, _person(), repo, notifier)
    assert q is not None and q.channel == "inbox" and notifier.asked == []


@pytest.mark.asyncio
async def test_milestones_respect_system_channel():
    from hearth.domain.milestones import check_milestones
    sent = []

    class Repo:
        def __init__(self):
            self.settings = {}
        def persons(self):
            return [Person(id="admin", name="Admin", notify_service="m_a",
                           notify_system=True),
                    Person(id="quiet", name="Quiet", notify_service="m_q",
                           notify_system=False)]
        def get_setting(self, k, d=None): return self.settings.get(k, d)
        def set_setting(self, k, v): self.settings[k] = v
        def clusters(self, status=None): return []
        def models(self, person=None): return []

    class Tsdb:
        def count_raw_events(self, hours=2): return 999
        def read_predictions(self, *a): return []

    class Notifier:
        async def notify(self, person, title, message, data=None):
            sent.append(person.id); return True

    await check_milestones(Repo(), Tsdb(), Notifier())
    assert sent == ["admin"]          # quiet member never pinged

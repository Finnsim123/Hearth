from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from hearth.domain.behaviour.digest import compose_digest, run_weekly_digest
from hearth.domain.behaviour.summary import summarize

NOW = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)


def _rows(spec):
    rows = []
    for smin, emin, state, mv in spec:
        t = smin
        while t < emin:
            rows.append({"time": (NOW - timedelta(days=3) + timedelta(minutes=t)).isoformat(),
                         "smoothed": state, "predicted": state, "model_version": mv})
            t += 30
    return rows


def test_compose_digest_is_descriptive_and_honest():
    rows = _rows([(0, 480, "asleep", "fact-v0"), (480, 600, "cooking", "m-v1"),
                  (600, 1080, "away", "fact-v0")])
    s = summarize("alice", rows, tz="UTC", now=NOW)
    title, msg = compose_digest("Alice", s, [], None, {"asleep": "Asleep",
                                "cooking": "Cooking", "away": "Away"})
    assert title == "Alice's week at home"
    assert "Asleep" in msg and "Cooking" in msg          # time budget
    assert "Slept about" in msg                           # sleep fact
    assert "% of the week I could classify" in msg        # honesty footer


class _Person:
    def __init__(self, pid, notify_system):
        self.id = pid; self.name = pid.title()
        self.enabled = True; self.has_device = True; self.notify_system = notify_system


class FakeRepo:
    def __init__(self, persons, settings=None):
        self._p = persons; self._s = settings or {}
    def get_setting(self, k, d=None): return self._s.get(k, d)
    def set_setting(self, k, v): self._s[k] = v
    def persons(self): return self._p
    def activities(self): return []
    def bindings(self): return []


class FakeTSDB:
    def __init__(self, rows): self._rows = rows
    def read_predictions(self, pid, start, end): return self._rows
    def read_raw(self, *a, **k):
        import pandas as pd
        return pd.DataFrame()


class FakeNotifier:
    def __init__(self): self.sent = []
    async def notify(self, person, title, message):
        self.sent.append((person.id, title, message)); return True


def test_run_weekly_digest_respects_opt_in_gates():
    rows = _rows([(0, 480, "asleep", "fact-v0"), (480, 600, "cooking", "m-v1")])
    # global flag off → nothing sent even with an opted-in person
    repo = FakeRepo([_Person("alice", True)])
    n = FakeNotifier()
    assert asyncio.run(run_weekly_digest(repo, FakeTSDB(rows), n, now=NOW)) == 0
    assert n.sent == []

    # flag on, but person not opted into the system channel → skipped
    repo2 = FakeRepo([_Person("bob", False)], {"behaviour.digest.enabled": True})
    n2 = FakeNotifier()
    assert asyncio.run(run_weekly_digest(repo2, FakeTSDB(rows), n2, now=NOW)) == 0

    # flag on + opted in → sent
    repo3 = FakeRepo([_Person("alice", True)], {"behaviour.digest.enabled": True})
    n3 = FakeNotifier()
    assert asyncio.run(run_weekly_digest(repo3, FakeTSDB(rows), n3, now=NOW)) == 1
    assert n3.sent[0][0] == "alice" and "week at home" in n3.sent[0][1]

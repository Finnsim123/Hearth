from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hearth.domain.behaviour.household import (
    cooccurrence,
    opted_in_ids,
    set_share,
    shares,
)

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)


def _rows(spec):
    rows = []
    for smin, emin, state in spec:
        t = smin
        while t < emin:
            rows.append({"time": (BASE + timedelta(minutes=t)).isoformat(),
                         "smoothed": state, "model_version": "m-v1"})
            t += 30
    return rows


def test_cooccurrence_conditional_top_partner_activity():
    # While A cooks (0-120), B watches tv the whole time → tv 100%.
    a = _rows([(0, 120, "cooking")])
    b = _rows([(0, 120, "tv")])
    items = {it.a: it for it in cooccurrence(a, b)}
    assert items["cooking"].b == "tv"
    assert items["cooking"].frac == 1.0
    assert items["cooking"].minutes == 120


def test_cooccurrence_ignores_unknown_and_nonoverlap():
    a = _rows([(0, 120, "cooking")])
    b = _rows([(60, 180, "tv")])               # only 60m overlaps
    items = {it.a: it for it in cooccurrence(a, b, min_state_min=30)}
    assert items["cooking"].minutes == 60


class FakeRepo:
    def __init__(self, persons):
        self._p = persons; self._s = {}
    def get_setting(self, k, d=None): return self._s.get(k, d)
    def set_setting(self, k, v): self._s[k] = v
    def persons(self): return self._p


class P:
    def __init__(self, pid): self.id = pid; self.enabled = True


def test_consent_helpers_and_opted_in():
    repo = FakeRepo([P("alice"), P("bob"), P("cara")])
    assert opted_in_ids(repo) == []
    set_share(repo, "alice", True)
    set_share(repo, "bob", True)
    assert shares(repo, "alice") and not shares(repo, "cara")
    assert sorted(opted_in_ids(repo)) == ["alice", "bob"]

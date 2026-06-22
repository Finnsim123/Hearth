from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hearth.domain import advisories as A
from hearth.domain import events as E
from hearth.domain import health as H


class FakeRepo:
    def __init__(self): self._s = {}
    def get_setting(self, k, d=None): return self._s.get(k, d)
    def set_setting(self, k, v): self._s[k] = v


# ── advisories ────────────────────────────────────────────────────────────────
def test_advisory_record_dismiss_clear():
    r = FakeRepo()
    A.record_advisory(r, "coverage:blindspot", "Blind in the kitchen", "add a sensor",
                      severity="info")
    A.record_advisory(r, "foundational:bed", "Bed sensor unreliable", "demoted",
                      severity="warn")
    assert [a["kind"] for a in A.active_advisories(r)] == ["foundational:bed", "coverage:blindspot"]
    # worst at/above warn skips the info-level blind spot
    assert A.worst_advisory(r)["kind"] == "foundational:bed"
    # dismiss snoozes it
    A.dismiss_advisory(r, "foundational:bed", days=7)
    assert A.worst_advisory(r) is None
    assert [a["kind"] for a in A.active_advisories(r)] == ["coverage:blindspot"]
    # producer clears it entirely
    A.clear_advisory(r, "coverage:blindspot")
    assert A.active_advisories(r) == []


def test_changed_advisory_resurfaces_after_dismiss():
    r = FakeRepo()
    A.record_advisory(r, "model:alice", "Confidence off", "ece high", severity="warn")
    A.dismiss_advisory(r, "model:alice")
    assert A.worst_advisory(r) is None
    A.record_advisory(r, "model:alice", "Accuracy low", "new problem", severity="warn")
    assert A.worst_advisory(r)["kind"] == "model:alice"     # un-snoozed by the change


# ── events ────────────────────────────────────────────────────────────────────
def test_events_ring_buffer_newest_first():
    r = FakeRepo()
    for i in range(3):
        E.record_event(r, "test", f"event {i}")
    evs = E.list_events(r)
    assert [e["title"] for e in evs] == ["event 2", "event 1", "event 0"]


# ── health multi-issue + severity ─────────────────────────────────────────────
def test_health_surfaces_worst_of_concurrent_issues():
    r = FakeRepo()
    H.record_issue(r, "system_heavy", "Running heavy", "load high", severity="warn")
    H.record_issue(r, "influx_unreachable", "DB down", "no influx", severity="critical")
    assert H.current_issue(r)["kind"] == "influx_unreachable"   # critical beats warn
    assert len(H.active_issues(r)) == 2
    H.clear_issue(r, "influx_unreachable")
    assert H.current_issue(r)["kind"] == "system_heavy"          # warn remains


def test_health_issue_expires():
    r = FakeRepo()
    H.record_issue(r, "x", "t", "d")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    r._s["system.issues"]["x"]["at"] = stale
    assert H.current_issue(r) is None


def test_health_migrates_legacy_single_issue():
    r = FakeRepo()
    r._s["system.issue"] = {"kind": "legacy", "title": "old", "detail": "d",
                            "at": datetime.now(timezone.utc).isoformat()}
    assert H.current_issue(r)["kind"] == "legacy"

"""Buddy phase resolver — friendly narration of the current pipeline phase."""
from __future__ import annotations

from hearth.domain.buddy import buddy_state


class _Repo:
    def __init__(self, settings=None):
        self._s = settings or {}
    def get_setting(self, key, default=None):
        return self._s.get(key, default)
    def persons(self):
        return []
    def models(self, person=None):
        return []
    def bindings(self):
        return []
    def open_questions(self, person=None):
        return []


def test_waiting_when_nothing_connected():
    out = buddy_state(_Repo(), None)
    assert out["phase"] == "waiting" and out["tone"] == "work"


def test_fasttrack_importing_is_setup_phase():
    out = buddy_state(_Repo({"fasttrack.status": {"stage": "importing", "span_days": 150}}), None)
    assert out["phase"] == "setup:importing"
    assert "history" in out["title"].lower()
    assert out["progress"] is not None


def test_fasttrack_building_progress_tracks_chunks():
    out = buddy_state(_Repo({"fasttrack.status":
                             {"stage": "building_features", "chunk": 3, "of": 6}}), None)
    assert out["phase"] == "setup:building_features"
    assert 0.4 <= out["progress"] <= 0.7


def test_live_issue_surfaces_and_stale_one_does_not():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    issue = {"kind": "ha_unreachable", "title": "I can't reach Home Assistant",
             "detail": "Lost the connection", "cta": {"label": "Settings", "href": "/settings"}}
    fresh = buddy_state(_Repo({"system.issue": {**issue, "at": now.isoformat()}}), None)
    assert fresh["phase"] == "issue:ha_unreachable" and fresh["tone"] == "alert"
    assert "Home Assistant" in fresh["title"] and fresh["cta"]["href"] == "/settings"
    # an old, already-recovered issue must NOT keep nagging
    old = buddy_state(_Repo({"system.issue": {**issue, "at": (now - timedelta(hours=2)).isoformat()}}), None)
    assert not old["phase"].startswith("issue:")


def test_failed_is_error():
    out = buddy_state(_Repo({"fasttrack.status": {"stage": "failed", "error": "boom"}}), None)
    assert out["phase"] == "error" and out["tone"] == "error"


def test_retraining_flag():
    out = buddy_state(_Repo({"training.status": {"running": True}}), None)
    assert out["phase"] == "retraining"


def test_llm_credit_error_surfaces_with_link():
    out = buddy_state(_Repo({"llm.status": {"ok": False, "code": 402}}), None)
    assert out["phase"] == "llm_error" and out["tone"] == "alert"
    assert out["cta"] and out["cta"]["href"]
    # a healthy/absent status must NOT raise the warning
    assert buddy_state(_Repo({"llm.status": {"ok": True, "code": 200}}), None)["phase"] != "llm_error"
    # a connectivity failure (code 0 / 5xx) surfaces as "can't reach the AI"
    out = buddy_state(_Repo({"llm.status": {"ok": False, "code": 0}}), None)
    assert out["phase"] == "llm_error" and "reach" in out["title"].lower()


def test_onboarding_seed_phases_are_sequential_setup_phases():
    # during onboarding (fast-track pending) the seed sub-steps surface in order
    base = {"fasttrack.pending": {"source": "recorder"}}
    assert buddy_state(_Repo({**base, "seed.status": {"stage": "scanning"}}), None)["phase"] == "setup:scanning"
    assert buddy_state(_Repo({**base, "seed.status": {"stage": "triaging"}}), None)["phase"] == "setup:triaging"
    assert buddy_state(_Repo({**base, "seed.status": {"stage": "mapping"}}), None)["phase"] == "setup:mapping"
    # awaiting the user's approval parks at the sorting step
    out = buddy_state(_Repo({**base, "triage.awaiting": True}), None)
    assert out["phase"] == "setup:triaging" and out["tone"] == "ask"
    # once seed is done, fast-track takes over (not masked by the seed block)
    assert buddy_state(_Repo({**base, "seed.status": {"stage": "done"},
                              "fasttrack.status": {"stage": "training"}}), None)["phase"] == "setup:training"


def test_remap_after_retry_wins_over_stale_llm_error():
    # user clicked "Try again": seed re-runs while llm.status is still the old
    # failure. Buddy must narrate the live remap, not the stale credit warning.
    out = buddy_state(_Repo({"seed.status": {"stage": "mapping"},
                             "llm.status": {"ok": False, "code": 402}}), None)
    assert out["phase"] == "remap:mapping" and out["tone"] == "work"
    # a finished remap goes quiet (no lingering remap state)
    assert buddy_state(_Repo({"seed.status": {"stage": "done"}}), None)["phase"] != "remap:done"

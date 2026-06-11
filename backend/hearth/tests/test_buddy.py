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

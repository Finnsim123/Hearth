"""Two-way per-person controls: questions opt-out + manual override."""
from __future__ import annotations

from datetime import datetime, timezone

from datetime import timedelta

from hearth.domain.controls import (
    active_override, apply_command, override_is_labeling, override_prediction,
    override_set_at, parse_command, questions_disabled, set_override,
    set_questions_optout,
)
from hearth.domain.schemas import Prediction


class Repo:
    def __init__(self):
        self.s: dict = {}
    def get_setting(self, k, d=None):
        return self.s.get(k, d)
    def set_setting(self, k, v):
        self.s[k] = v


def test_questions_optout():
    r = Repo()
    assert questions_disabled(r, "alice") is False        # default: questions on
    set_questions_optout(r, "alice", True)
    assert questions_disabled(r, "alice") is True
    set_questions_optout(r, "alice", False)
    assert questions_disabled(r, "alice") is False


def test_override_set_clear_validate():
    r = Repo()
    assert active_override(r, "alice") is None
    assert set_override(r, "alice", "movie", {"movie", "home"}) == "movie"
    assert active_override(r, "alice") == "movie"
    assert set_override(r, "alice", "bogus", {"movie", "home"}) is None    # unknown -> cleared
    assert active_override(r, "alice") is None
    set_override(r, "alice", "home", {"movie", "home"})
    assert set_override(r, "alice", "auto", {"movie", "home"}) is None     # auto -> cleared


def test_override_labeling_freshness():
    r = Repo()
    set_override(r, "alice", "movie", {"movie"})
    at = override_set_at(r, "alice")
    assert at is not None
    # fresh right after setting -> labels; an hour-plus later -> pin only
    assert override_is_labeling(r, "alice", at + timedelta(minutes=10)) is True
    assert override_is_labeling(r, "alice", at + timedelta(minutes=90)) is False
    # a bare-slug override (no timestamp) never labels
    r.s["override.alice"] = "movie"
    assert active_override(r, "alice") == "movie"
    assert override_set_at(r, "alice") is None
    assert override_is_labeling(r, "alice", at) is False


def test_override_prediction():
    pred = Prediction(person_id="alice", window_ts=datetime.now(timezone.utc),
                      model_version="alice-v2", predicted="cooking", smoothed="cooking",
                      confidence=0.4, probabilities={"cooking": 0.4, "home": 0.6})
    o = override_prediction(pred, "movie")
    assert o.predicted == "movie" and o.smoothed == "movie" and o.confidence == 1.0
    assert o.probabilities == {"movie": 1.0} and o.model_version == "override"


def test_parse_and_apply_command():
    assert parse_command("hearth/alice/questions/set") == ("alice", "questions")
    assert parse_command("hearth/alice/override/set") == ("alice", "override")
    assert parse_command("hearth/alice/activity") is None
    assert parse_command("nonsense") is None

    r = Repo()
    assert apply_command(r, "hearth/alice/questions/set", "OFF", set()) == ("questions", "alice", "OFF")
    assert questions_disabled(r, "alice") is True
    assert apply_command(r, "hearth/alice/questions/set", "ON", set()) == ("questions", "alice", "ON")
    assert questions_disabled(r, "alice") is False

    assert apply_command(r, "hearth/alice/override/set", "movie", {"movie"}) == ("override", "alice", "movie")
    assert active_override(r, "alice") == "movie"
    assert apply_command(r, "hearth/alice/override/set", "auto", {"movie"}) == ("override", "alice", "auto")
    assert active_override(r, "alice") is None

    assert apply_command(r, "hearth/alice/activity", "x", set()) is None   # not a command

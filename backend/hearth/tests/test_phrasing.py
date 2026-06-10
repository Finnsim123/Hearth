from __future__ import annotations

from datetime import datetime, timezone

from hearth.domain.labeling.phrasing import button_titles, phrase_question, verb_phrase
from hearth.domain.schemas import Activity

ACTS = [Activity(slug="movie", name="Movie", phrase="watching a movie"),
        Activity(slug="cooking", name="Cooking"),
        Activity(slug="sleeping", name="Sleeping", phrase="lying in bed")]
TS = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


def test_confident_mode_uses_verb_phrase():
    msg, opts = phrase_question({"movie": 0.7, "cooking": 0.2, "sleeping": 0.1}, ACTS, TS)
    assert "watching a movie" in msg and msg.endswith("?")
    assert opts[0] == "movie"


def test_toss_up_names_both_candidates():
    msg, opts = phrase_question({"cooking": 0.42, "sleeping": 0.40, "movie": 0.18}, ACTS, TS)
    assert "cooking" in msg and "lying in bed" in msg
    assert opts[:2] == ["cooking", "sleeping"]


def test_unsure_mode_open_question():
    msg, opts = phrase_question({"cooking": 0.35, "sleeping": 0.33, "movie": 0.32}, ACTS, TS)
    # gap < toss-up threshold -> toss-up; force flat single-class low conf:
    msg2, _ = phrase_question({"cooking": 0.3}, ACTS, TS)
    assert msg2.endswith("?")
    assert len(opts) == 3


def test_templates_vary_across_windows_but_stable_per_window():
    probs = {"movie": 0.8, "cooking": 0.1}
    msgs = {phrase_question(probs, ACTS,
                            datetime(2026, 6, 1, h, 0, tzinfo=timezone.utc))[0]
            for h in (8, 9, 10)}
    assert len(msgs) > 1                                  # varies over time
    assert phrase_question(probs, ACTS, TS) == phrase_question(probs, ACTS, TS)


def test_custom_activity_falls_back_to_name():
    assert verb_phrase("gaming", [Activity(slug="gaming", name="Gaming")]) == "gaming"
    assert button_titles(["movie", "gaming"], ACTS) == ["Movie", "Gaming"]

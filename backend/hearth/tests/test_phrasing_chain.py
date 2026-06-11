"""Progressive question options: confident Yes/No, toss-up + Other, and the
follow-up chain that walks the rest of the taxonomy until one is picked."""
from __future__ import annotations

from datetime import datetime, timezone

from hearth.domain.labeling.phrasing import (next_batch, option_universe,
                                             root_options)
from hearth.domain.schemas import Activity

TS = datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc)
ACTS = [Activity(slug=s, name=s.capitalize()) for s in
        ("home", "away", "cooking", "eating", "movie", "working", "sleeping")]


def test_universe_is_best_guess_first_then_all_enabled():
    probs = {"cooking": 0.5, "home": 0.3}
    uni = option_universe(probs, ACTS)
    assert uni[:2] == ["cooking", "home"]          # ranked by probability
    assert set(uni) == {a.slug for a in ACTS}      # every enabled activity reachable
    assert len(uni) == len(set(uni))               # de-duplicated


def test_confident_offers_one_option_with_more():
    # clear winner → single option (rendered as Yes/No), escape still available
    msg, opts, has_more = root_options({"cooking": 0.9, "home": 0.05}, ACTS, TS)
    assert opts == ["cooking"] and has_more is True


def test_tossup_offers_two_options_plus_more():
    msg, opts, has_more = root_options({"cooking": 0.45, "eating": 0.4}, ACTS, TS)
    assert opts == ["cooking", "eating"] and has_more is True


def test_followup_chain_excludes_asked_and_terminates():
    probs = {"cooking": 0.45, "eating": 0.4}
    asked = ["cooking", "eating"]                   # already shown on the root ask
    seen = set(asked)
    steps = 0
    while True:
        batch, has_more = next_batch(probs, ACTS, list(seen))
        assert batch and not (set(batch) & seen)    # never repeats an option
        seen.update(batch)
        steps += 1
        if not has_more:
            break
        assert len(batch) <= 2                       # leaves a slot for "Other"
        assert steps < 10                            # always terminates
    assert seen == {a.slug for a in ACTS}            # every activity becomes reachable
    # the final batch (no escape) is forced and small enough for HA's 3 buttons
    assert len(batch) <= 3

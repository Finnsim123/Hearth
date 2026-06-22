from __future__ import annotations

from hearth.domain.labeling.dedupe import canonical_activity, dedupe_suggestions
from hearth.domain.schemas import Activity


ACTS = [
    Activity(id=1, slug="away", name="Away"),
    Activity(id=2, slug="asleep", name="Asleep"),
    Activity(id=3, slug="home", name="Home"),
    Activity(id=4, slug="cooking", name="Cooking"),
]
PERSONS = ["Alex", "Nora"]


def test_person_specific_away_folds_into_away():
    assert canonical_activity("Alex out of the house", ACTS, PERSONS) == "away"
    assert canonical_activity("out of the house", ACTS, PERSONS) == "away"
    assert canonical_activity("not home", ACTS, PERSONS) == "away"
    assert canonical_activity("gone", ACTS, PERSONS) == "away"


def test_sleep_synonyms_fold_into_asleep():
    assert canonical_activity("sleeping", ACTS, PERSONS) == "asleep"
    assert canonical_activity("Alex in bed", ACTS, PERSONS) == "asleep"
    assert canonical_activity("afternoon nap", ACTS, PERSONS) == "asleep"


def test_exact_existing_match():
    assert canonical_activity("Cooking", ACTS, PERSONS) == "cooking"
    assert canonical_activity("cooking", ACTS, PERSONS) == "cooking"


def test_genuinely_new_returns_none():
    assert canonical_activity("Woodworking", ACTS, PERSONS) is None
    assert canonical_activity("Playing piano", ACTS, PERSONS) is None


def test_reserved_only_matches_when_state_exists():
    no_away = [a for a in ACTS if a.slug != "away"]
    # with no 'away' activity, 'out of the house' is genuinely new (won't invent)
    assert canonical_activity("out of the house", no_away, PERSONS) is None


def test_dedupe_suggestions_rewrites_new_into_existing():
    sugg = [
        {"name": "Alex out of the house", "slug": None, "kind": "new", "confidence": 0.8},
        {"name": "Woodworking", "slug": None, "kind": "new", "confidence": 0.6},
    ]
    out = dedupe_suggestions(sugg, ACTS, PERSONS)
    assert out[0]["slug"] == "away" and out[0]["kind"] == "existing"
    assert out[0]["name"] == "Away"                 # shows the real activity name
    assert out[1]["slug"] is None                   # genuinely new, left alone

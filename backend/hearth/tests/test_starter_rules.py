"""Starter rules: generated from bindings, no entity names, sensible labels."""
from __future__ import annotations

import pandas as pd

from hearth.domain.labeling.rules import bootstrap_labels
from hearth.domain.labeling.starter_rules import starter_rules
from hearth.domain.schemas import Activity, Binding, Role

ACTS = [Activity(slug=s, name=s) for s in
        ("sleeping", "away", "home", "cooking", "movie", "eating")]
BINDINGS = [
    Binding(entity_id="person.alice", role=Role.PERSON, name="alice_loc", person_id="alice"),
    Binding(entity_id="sensor.bed_a", role=Role.BED, name="bed_a", person_id="alice"),
    Binding(entity_id="binary_sensor.kitchen_mm", role=Role.PRESENCE, name="kitchen_mm",
            room="Kitchen"),
    Binding(entity_id="media_player.tv", role=Role.MEDIA, name="tv", room="Living room"),
    Binding(entity_id="binary_sensor.couch", role=Role.PRESENCE, name="couch",
            room="Living room"),
]


def test_rules_cover_supported_activities():
    rules = starter_rules(BINDINGS, ACTS)
    by_activity = {r.activity_slug for r in rules}
    assert {"away", "sleeping", "cooking", "movie"} <= by_activity
    assert "eating" not in by_activity            # no dining binding -> no rule
    # no entity ids anywhere in predicates
    import json
    assert "person.alice" not in json.dumps([r.predicate for r in rules])


def test_generated_rules_label_a_day_correctly():
    rules = starter_rules(BINDINGS, ACTS)
    idx = pd.date_range("2026-06-01", periods=48, freq="30min", tz="UTC")
    hours = idx.hour
    feats = pd.DataFrame({
        "hour_of_day": hours.astype(float),
        "alice_loc_home_last": [0.0 if 9 <= h < 17 else 1.0 for h in hours],
        "bed_a_occupied": [1.0 if (h >= 23 or h < 7) else 0.0 for h in hours],
        "kitchen_mm_frac": [0.6 if h == 18 else 0.0 for h in hours],
        "tv_playing": [1.0 if h in (20, 21) else 0.0 for h in hours],
        "couch_frac": [0.8 if h in (20, 21) else 0.0 for h in hours],
    }, index=idx)
    labels = bootstrap_labels(rules, feats, "alice")
    assert labels[hours == 2].iloc[0] == "sleeping"
    assert labels[hours == 12].iloc[0] == "away"
    assert labels[hours == 18].iloc[0] == "cooking"
    assert labels[hours == 20].iloc[0] == "movie"
    assert labels[hours == 8].iloc[0] == "home"
    assert labels.nunique() >= 4                   # trainable, not single-class


def test_missing_person_data_is_not_away():
    """Prototype lesson #7: no person data = UNKNOWN (-1), never 'away' (0).
    The away rule (home_last == 0) must not fire on the sentinel."""
    from hearth.domain.features.registry import recipe_for
    from hearth.domain.schemas import Role
    recipe = recipe_for(Role.PERSON)
    assert recipe.absence_value == -1.0
    assert recipe.ffill_limit_min >= 7 * 24 * 60       # fill across the lookback
    rules = starter_rules(BINDINGS, ACTS)
    idx = pd.date_range("2026-06-01 12:00", periods=2, freq="30min", tz="UTC")
    feats = pd.DataFrame({"hour_of_day": [12.0, 12.5],
                          "alice_loc_home_last": [-1.0, -1.0],   # sentinel
                          "bed_a_occupied": [0.0, 0.0],
                          "kitchen_mm_frac": [0.0, 0.0],
                          "tv_playing": [0.0, 0.0],
                          "couch_frac": [0.0, 0.0]}, index=idx)
    labels = bootstrap_labels(rules, feats, "alice")
    assert (labels != "away").all()

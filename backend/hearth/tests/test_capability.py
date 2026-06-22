from __future__ import annotations

from hearth.domain.capability import assess_capability


ACTS = [{"slug": "away", "name": "Away"}, {"slug": "cooking", "name": "Cooking"},
        {"slug": "eating", "name": "Eating"}, {"slug": "movie", "name": "Movie"}]


def _metrics(per_class, confusion, *, n_gold=80, status="validated", flat=0.3, mine=0.7):
    return {"per_class": per_class, "confusion": confusion, "n_gold": n_gold,
            "validation_status": status, "accuracy_gold": mine,
            "flat_baseline": {"accuracy_gold": flat}}


def test_no_model_is_honest_learning():
    r = assess_capability("alex", ACTS, None)
    assert r.has_model is False and r.reliable == []
    assert all(a.tier == "learning" for a in r.activities)
    assert "no reliable predictions" in r.overall.lower()


def test_reliable_when_strong_and_validated():
    pc = {"away": {"f1": 0.95, "support": 200}}
    conf = {"labels": ["away"], "matrix": [[200]]}
    r = assess_capability("alex", [{"slug": "away", "name": "Away"}], _metrics(pc, conf))
    a = r.activities[0]
    assert a.tier == "reliable" and "away" in r.reliable


def test_confused_pair_is_unreliable_with_remedy():
    # cooking and eating blur badly (50% mutual confusion)
    pc = {"cooking": {"f1": 0.45, "support": 100}, "eating": {"f1": 0.44, "support": 100}}
    conf = {"labels": ["cooking", "eating"], "matrix": [[50, 50], [50, 50]]}
    r = assess_capability("alex", [{"slug": "cooking", "name": "Cooking"},
                                   {"slug": "eating", "name": "Eating"}],
                          _metrics(pc, conf))
    cook = next(a for a in r.activities if a.slug == "cooking")
    assert cook.tier == "unreliable"
    assert cook.confused_with == "eating"
    assert cook.remedy and "separate" in cook.remedy.lower()
    assert "cooking" in r.needs_help


def test_loses_to_flat_baseline_is_unreliable():
    pc = {"movie": {"f1": 0.55, "support": 80}}
    conf = {"labels": ["movie"], "matrix": [[80]]}
    # model accuracy below the flat baseline → no better than guessing
    r = assess_capability("alex", [{"slug": "movie", "name": "Movie"}],
                          _metrics(pc, conf, mine=0.25, flat=0.40))
    assert r.activities[0].tier == "unreliable"


def test_low_support_stays_learning_not_condemned():
    pc = {"cooking": {"f1": 0.2, "support": 4}}      # barely any examples
    conf = {"labels": ["cooking"], "matrix": [[4]]}
    r = assess_capability("alex", [{"slug": "cooking", "name": "Cooking"}], _metrics(pc, conf))
    assert r.activities[0].tier == "learning"        # not enough data ≠ broken


def test_blind_when_room_has_no_sensor():
    class Gap:
        kind = "ghost_room"; activities = ["cooking"]; recommendation = "Add a sensor in the kitchen."
    pc = {"cooking": {"f1": 0.9, "support": 100}}
    conf = {"labels": ["cooking"], "matrix": [[100]]}
    r = assess_capability("alex", [{"slug": "cooking", "name": "Cooking"}],
                          _metrics(pc, conf), coverage_gaps=[Gap()])
    assert r.activities[0].tier == "blind"
    assert "kitchen" in (r.activities[0].remedy or "")

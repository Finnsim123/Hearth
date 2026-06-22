from __future__ import annotations

from hearth.domain.coverage.advisor import (
    SensorGap,
    confused_pairs,
    detect_gaps,
    phrase_gap,
    room_roles,
)
from hearth.domain.schemas import Binding, Role

# kitchen has NO sensor; living has presence; bedroom has a bed sensor
BINDINGS = [
    Binding(entity_id="binary_sensor.couch", role=Role.PRESENCE, name="couch", room="living"),
    Binding(entity_id="sensor.bed", role=Role.BED, name="bed", room="bedroom"),
]

CONFUSION = {
    "labels": ["cooking", "eating", "sleeping"],
    "matrix": [[10, 6, 0],
               [5, 12, 0],
               [0, 0, 30]],
}
ACT_ROOM = {"cooking": "kitchen", "eating": "kitchen", "sleeping": "bedroom"}


def test_room_roles():
    rr = room_roles(BINDINGS)
    assert rr["living"] == {Role.PRESENCE}
    assert "kitchen" not in rr


def test_confused_pairs_symmetric_rate():
    pairs = confused_pairs(CONFUSION, min_rate=0.15)
    assert pairs and pairs[0][:2] == ("cooking", "eating")
    # (6+5)/(16+17) = 0.333
    assert abs(pairs[0][2] - 0.333) < 0.01


def test_detect_confused_pair_suggests_kitchen_sensor():
    gaps = detect_gaps(CONFUSION, ACT_ROOM, {}, BINDINGS)
    cp = next(g for g in gaps if g.kind == "confused_pair")
    assert cp.room == "kitchen"
    assert set(cp.activities) == {"cooking", "eating"}
    assert cp.suggested_role == Role.PRESENCE        # kitchen missing all direct roles
    assert "kitchen" in cp.recommendation
    assert "cooking" in cp.recommendation and "eating" in cp.recommendation


def test_weak_evidence_gap():
    gaps = detect_gaps(CONFUSION, ACT_ROOM, {"sleeping": 0.7}, BINDINGS, min_confusion=0.99)
    we = [g for g in gaps if g.kind == "weak_evidence"]
    assert we and we[0].activities == ["sleeping"] and we[0].room == "bedroom"


def test_ghost_room_gap():
    gaps = detect_gaps(CONFUSION, ACT_ROOM, {}, BINDINGS, min_confusion=0.99,
                       referenced_rooms={"garage"})
    gr = [g for g in gaps if g.kind == "ghost_room"]
    assert gr and gr[0].room == "garage" and "garage" in gr[0].recommendation


def test_ranked_by_severity():
    gaps = detect_gaps(CONFUSION, ACT_ROOM, {"cooking": 0.9}, BINDINGS,
                       referenced_rooms={"garage"})
    sev = [g.severity for g in gaps]
    assert sev == sorted(sev, reverse=True)


def test_phrase_is_deterministic_and_human():
    g = SensorGap(kind="confused_pair", severity=0.5, room="kitchen",
                  activities=["cooking", "eating"], suggested_role=Role.POWER)
    s = phrase_gap(g)
    assert "kitchen" in s and "smart plug" in s

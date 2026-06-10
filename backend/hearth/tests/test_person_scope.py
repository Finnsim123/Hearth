"""Person-scoping: one member's alarm/phone never feeds another's model."""
from __future__ import annotations

import pandas as pd

from hearth.domain.features.person_scope import (
    PERSONAL_ROLES, binding_owner, drop_foreign_personal, foreign_personal_prefixes)
from hearth.domain.schemas import Binding, Person, Role

ALICE = Person(id="alice", name="Alice")
BOB = Person(id="bob", name="Bob")
PERSONS = [ALICE, BOB]


def _b(name, role, entity=None, person=None):
    return Binding(entity_id=entity or f"sensor.{name}", role=role,
                   name=name, person_id=person)


def test_owner_explicit_beats_tokens():
    b = _b("wekker_main", Role.ALARM_TIME, person="bob")
    assert binding_owner(b, PERSONS) == "bob"


def test_owner_from_name_token_any_language():
    assert binding_owner(_b("alice_wekker", Role.ALARM_TIME), PERSONS) == "alice"
    assert binding_owner(_b("focus", Role.FOCUS,
                            entity="binary_sensor.iphone_von_bob_focus"), PERSONS) == "bob"
    # token match, not substring: "alice" must not claim "chalice_lamp"
    assert binding_owner(_b("chalice_lamp", Role.LIGHT), PERSONS) is None


def test_foreign_personal_dropped_context_kept():
    bindings = [
        _b("bob_wekker", Role.ALARM_TIME),          # bob's alarm → drop for alice
        _b("bob_steps", Role.STEPS),                # bob's steps → drop for alice
        _b("alice_wekker", Role.ALARM_TIME),        # alice's own → keep
        _b("bed_bob_side", Role.BED),               # context role → keep (schedule context)
        _b("kitchen", Role.PRESENCE),               # shared → keep
    ]
    feats = pd.DataFrame([[1] * 7], columns=[
        "bob_wekker_minutes_until", "bob_steps_delta", "bob_steps_mean",
        "alice_wekker_minutes_until", "bed_bob_side_pressure_max",
        "kitchen_presence_frac", "hour_of_day"])
    out, dropped = drop_foreign_personal(feats, bindings, PERSONS, "alice")
    assert set(dropped) == {"bob_wekker_minutes_until", "bob_steps_delta", "bob_steps_mean"}
    assert "bed_bob_side_pressure_max" in out.columns      # partner bed = context
    assert "alice_wekker_minutes_until" in out.columns     # own alarm = signal


def test_unowned_personal_sensor_is_kept_for_everyone():
    # a shared/unclaimed alarm (no name match, no person_id) stays — better to
    # keep ambiguous signal than silently drop a single-person household's data
    bindings = [_b("wekker", Role.ALARM_TIME)]
    assert foreign_personal_prefixes(bindings, PERSONS, "alice") == set()


def test_prefix_match_is_exact_not_substring():
    # binding "bob_step" must not drop "bob_stepper_watts" (different binding)
    bindings = [_b("bob_step", Role.STEPS)]
    feats = pd.DataFrame([[1, 2]], columns=["bob_step_delta", "bob_stepper_watts"])
    out, dropped = drop_foreign_personal(feats, bindings, PERSONS, "alice")
    assert dropped == ["bob_step_delta"] and "bob_stepper_watts" in out.columns

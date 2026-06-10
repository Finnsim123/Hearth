"""Evidence tiers: profile math, column matching, weak-evidence labeling."""
from __future__ import annotations

import pandas as pd

from hearth.domain.features.evidence import (
    WEAK_DIRECT_SHARE, binding_tiers, evidence_label, evidence_profile,
    tier_of_column, window_evidence)
from hearth.domain.schemas import Binding, Role


def _b(name, role, **opts):
    return Binding(entity_id=f"sensor.{name}", role=role, name=name,
                   options=opts)

BINDINGS = [
    _b("bed_left", Role.BED),                 # tier 1
    _b("kitchen", Role.PRESENCE),             # tier 1
    _b("coffee", Role.POWER),                 # tier 2
    _b("co2", Role.ENV),                      # tier 3
    _b("odd_env", Role.ENV, tier=1),          # user/LLM override → 1
]


def test_role_tiers_and_override():
    tiers = binding_tiers(BINDINGS)
    assert tiers["bed_left"] == 1 and tiers["coffee"] == 2 and tiers["co2"] == 3
    assert tiers["odd_env"] == 1                       # appealable trust


def test_column_matching_longest_prefix_and_prior():
    tiers = binding_tiers(BINDINGS + [_b("bed", Role.ENV)])
    assert tier_of_column("bed_left_pressure_max", tiers) == 1   # bed_left beats bed
    assert tier_of_column("hour_of_day", tiers) == 0             # prior
    assert tier_of_column("cooking_signal", tiers) == 0          # composite → prior


def test_profile_normalizes_and_splits_mass():
    profile = evidence_profile(
        {"bed_left_pressure_max": 0.5, "co2_delta": 0.3, "hour_of_day": 0.2},
        BINDINGS)
    assert abs(sum(profile.values()) - 1.0) < 1e-6
    assert profile["direct"] == 0.5 and profile["ambient"] == 0.3
    assert profile["prior"] == 0.2


def test_window_evidence_and_labels():
    strong = pd.Series({"bed_left_pressure_max": 0.8, "co2_delta": 0.2})
    weak = pd.Series({"co2_delta": -0.7, "hour_of_day": 0.3})  # sign ignored
    assert window_evidence(strong, BINDINGS) == 0.8
    assert window_evidence(weak, BINDINGS) == 0.0
    assert evidence_label(0.8) == "strong"
    assert evidence_label(0.3) == "mixed"
    assert evidence_label(WEAK_DIRECT_SHARE - 0.01) == "weak"

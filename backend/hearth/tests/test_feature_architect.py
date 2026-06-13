"""Feature architect — pure prompt/parse/audit (no network)."""
from __future__ import annotations

from hearth.domain.features.validate import validate_spec
from hearth.domain.onboarding.feature_architect import (
    assemble_spec, audit_reliability, composite_prompt, feature_prompt,
    parse_features, parse_selections, selection_prompt,
)
from hearth.domain.schemas import InfoTier, Role


CATALOG = [
    {"entity_id": "binary_sensor.sofa",
     "metadata": {"domain": "binary_sensor", "device_class": "occupancy",
                  "friendly_name": "Sofa"},
     "stats": {"value_type": "boolean", "distinct_values": 2, "changes_per_day": 30,
               "flatline_frac": 0.2}},
    {"entity_id": "sensor.dead",
     "metadata": {"domain": "sensor", "friendly_name": "Stuck"},
     "stats": {"value_type": "numeric_continuous", "distinct_values": 1,
               "changes_per_day": 0, "flatline_frac": 1.0}},
]


def test_prompts_inject_context():
    sp = selection_prompt(CATALOG, ["movie", "cooking"], ["alice"])
    assert "binary_sensor.sofa" in sp and "movie" in sp and "presence" in sp
    assert "flat=0.2" in sp                       # shared stats reach the prompt
    fp = feature_prompt([], mode="full")
    assert "occupancy_fraction" in fp and "window_slope" in fp   # full whitelist
    fp_cons = feature_prompt([], mode="conservative")
    assert "window_slope" not in fp_cons          # gated out in conservative


def test_audit_reliability_overrides_llm():
    # a stuck sensor is unusable no matter what the LLM claimed
    assert audit_reliability({"value_type": "numeric_continuous", "distinct_values": 1,
                              "flatline_frac": 1.0}, "ok") == "unusable"
    assert audit_reliability({"value_type": "unknown"}, "ok") == "unusable"
    # otherwise the LLM's (valid) call stands; junk values default to ok
    assert audit_reliability({"value_type": "boolean", "flatline_frac": 0.1}, "suspect") == "suspect"
    assert audit_reliability(None, "bogus") == "ok"


def test_parse_selections_types_and_reliability_guard():
    raw = [
        {"entity_id": "binary_sensor.sofa", "keep": True, "role": "presence",
         "info_tier": "T1", "reliability": "ok", "reason": "couch use"},
        {"entity_id": "sensor.dead", "keep": True, "role": "env",
         "info_tier": "T3", "reliability": "ok", "reason": "temp"},   # LLM said ok…
        {"entity_id": "sensor.ghost", "keep": True},                  # not in catalog -> dropped
    ]
    sels = parse_selections(raw, catalog=CATALOG, member_ids=["alice"])
    assert len(sels) == 2
    by = {s.entity_id: s for s in sels}
    assert by["binary_sensor.sofa"].role is Role.PRESENCE
    assert by["binary_sensor.sofa"].info_tier is InfoTier.DISCRETE_EVENT_GATE
    assert by["sensor.dead"].reliability == "unusable"   # …stats override to unusable


def test_parse_features_drops_malformed():
    raw = [
        {"name": "sofa_occ", "transform": "occupancy_fraction",
         "inputs": ["binary_sensor.sofa"], "info_tier": "T1", "window_min": 15},
        {"transform": "x"},                       # no name -> dropped
        {"name": "bad", "transform": "y", "inputs": "notalist"},   # inputs not list -> dropped
    ]
    feats = parse_features(raw)
    assert [f.name for f in feats] == ["sofa_occ"]
    assert feats[0].window_min == 15 and feats[0].info_tier is InfoTier.DISCRETE_EVENT_GATE


def test_assemble_and_validate_end_to_end():
    sels = parse_selections(
        [{"entity_id": "binary_sensor.sofa", "keep": True, "role": "presence",
          "info_tier": "T1", "reliability": "ok"}],
        catalog=CATALOG, member_ids=[])
    feats = parse_features(
        [{"name": "sofa_occ", "transform": "occupancy_fraction",
          "inputs": ["binary_sensor.sofa"], "info_tier": "T1"}])
    spec = assemble_spec(sels, feats, llm_model="m")
    clean, rejected = validate_spec(spec, mode="conservative")
    assert not rejected and [f.name for f in clean.features] == ["sofa_occ"]
    assert clean.created_by == "llm" and clean.llm_model == "m"

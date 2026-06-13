"""Schemas for the LLM data-analytics layer's output contract (Step 3 §d).
These types are not consumed yet (the builder lands in a later commit); this
just pins the contract: defaults, enum values, and round-trip validation."""
from __future__ import annotations

from hearth.domain.schemas import (
    EntitySelection, FeatureDef, FeatureSpec, InfoTier, Role,
)


def test_info_tier_codes():
    # The wire values are the T0..T5 codes the prompts and validators key on.
    assert [t.value for t in InfoTier] == ["T0", "T1", "T2", "T3", "T4", "T5"]


def test_entity_selection_minimal_and_full():
    drop = EntitySelection(entity_id="sensor.zigbee_chip_temp", keep=False,
                           reason="diagnostic")
    assert drop.role is None and drop.reliability == "ok"  # defaults for a dropped entity

    keep = EntitySelection(entity_id="binary_sensor.sofa", keep=True,
                           role=Role.PRESENCE, info_tier=InfoTier.DISCRETE_EVENT_GATE,
                           reliability="suspect", reason="flatlined 80% of the time")
    assert keep.role is Role.PRESENCE and keep.info_tier is InfoTier.DISCRETE_EVENT_GATE


def test_feature_spec_roundtrip():
    spec = FeatureSpec(
        created_by="llm", llm_model="anthropic/claude-sonnet-4.6",
        selections=[EntitySelection(entity_id="binary_sensor.sofa", keep=True,
                                    role=Role.PRESENCE,
                                    info_tier=InfoTier.DISCRETE_EVENT_GATE)],
        features=[FeatureDef(name="sofa_occupancy_fraction",
                             transform="occupancy_fraction",
                             inputs=["binary_sensor.sofa"], window_min=15,
                             info_tier=InfoTier.DISCRETE_EVENT_GATE,
                             rationale="time on the sofa separates movie from cooking",
                             expected_separates=["movie", "cooking"])],
    )
    # defaults
    assert spec.spec_version == "v1" and spec.features[0].origin == "llm"
    # round-trips through JSON with enums serialised as their string codes
    restored = FeatureSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.features[0].info_tier is InfoTier.DISCRETE_EVENT_GATE

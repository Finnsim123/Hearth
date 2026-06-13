"""The feature-spec safety walker (Step 3 §d validation scheme)."""
from __future__ import annotations

from hearth.domain.features import transforms as T
from hearth.domain.features.validate import validate_feature, validate_spec
from hearth.domain.schemas import (
    EntitySelection, FeatureDef, FeatureSpec, InfoTier, Role,
)


def _sel(eid, tier, *, keep=True, reliability="ok", role=Role.PRESENCE):
    return EntitySelection(entity_id=eid, keep=keep, role=role,
                           info_tier=tier, reliability=reliability)


def _spec(selections, features):
    return FeatureSpec(created_by="llm", selections=selections, features=features)


def test_accepts_valid_entity_feature():
    spec = _spec(
        [_sel("binary_sensor.sofa", InfoTier.DISCRETE_EVENT_GATE)],
        [FeatureDef(name="sofa_occ", transform="occupancy_fraction",
                    inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE)],
    )
    clean, rejected = validate_spec(spec)
    assert not rejected and [f.name for f in clean.features] == ["sofa_occ"]


def test_rejects_tier_mismatch_and_counter_raw_value():
    # window_mean (T3) on a T1 gate -> tier incompatible
    spec = _spec(
        [_sel("binary_sensor.sofa", InfoTier.DISCRETE_EVENT_GATE)],
        [FeatureDef(name="bad", transform="window_mean", inputs=["binary_sensor.sofa"],
                    info_tier=InfoTier.DISCRETE_EVENT_GATE)],
    )
    clean, rejected = validate_spec(spec)
    assert not clean.features and rejected[0][1].startswith("info tier")

    # window_mean on a T4 cumulative counter -> also tier-incompatible (counters
    # only expose rate/delta), so a raw-value feature on a counter can't slip in
    spec2 = _spec(
        [_sel("sensor.energy_kwh", InfoTier.CUMULATIVE_COUNTER, role=Role.POWER)],
        [FeatureDef(name="kwh_mean", transform="window_mean",
                    inputs=["sensor.energy_kwh"], info_tier=InfoTier.CUMULATIVE_COUNTER)],
    )
    assert not validate_spec(spec2)[0].features


def test_rejects_unknown_transform_bad_name_bad_params_window():
    sel = [_sel("binary_sensor.sofa", InfoTier.DISCRETE_EVENT_GATE)]
    cases = [
        FeatureDef(name="x", transform="nope", inputs=["binary_sensor.sofa"],
                   info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="Bad Name", transform="occupancy_fraction",
                   inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="p", transform="occupancy_fraction", inputs=["binary_sensor.sofa"],
                   params={"unexpected": 1}, info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="w", transform="occupancy_fraction", inputs=["binary_sensor.sofa"],
                   window_min=99999, info_tier=InfoTier.DISCRETE_EVENT_GATE),
    ]
    for f in cases:
        clean, rejected = validate_spec(_spec(sel, [f]))
        assert not clean.features and rejected


def test_input_must_be_kept_and_not_all_unusable():
    # input entity not in selections (or not kept)
    spec = _spec(
        [_sel("binary_sensor.sofa", InfoTier.DISCRETE_EVENT_GATE, keep=False)],
        [FeatureDef(name="f", transform="occupancy_fraction",
                    inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE)],
    )
    assert not validate_spec(spec)[0].features

    # kept but unusable -> dropped (its only input is unusable)
    spec2 = _spec(
        [_sel("binary_sensor.sofa", InfoTier.DISCRETE_EVENT_GATE, reliability="unusable")],
        [FeatureDef(name="f", transform="occupancy_fraction",
                    inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE)],
    )
    clean, rejected = validate_spec(spec2)
    assert not clean.features and rejected[0][1] == "all inputs flagged unusable"


def test_composite_references_and_ordering():
    sel = [_sel("binary_sensor.sofa", InfoTier.DISCRETE_EVENT_GATE),
           _sel("media_player.tv", InfoTier.STATE_MACHINE, role=Role.MEDIA)]
    base = [
        FeatureDef(name="sofa_occ", transform="occupancy_fraction",
                   inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="tv_on", transform="last_state", inputs=["media_player.tv"],
                   info_tier=InfoTier.STATE_MACHINE),
    ]
    good = FeatureDef(name="movie", transform="co_occurrence_and",
                      inputs=["sofa_occ", "tv_on"], params={"threshold": 0.5})
    clean, rejected = validate_spec(_spec(sel, base + [good]))
    assert [f.name for f in clean.features] == ["sofa_occ", "tv_on", "movie"]

    # composite referencing a feature defined AFTER it -> rejected (forward ref)
    early = FeatureDef(name="movie2", transform="co_occurrence_and",
                       inputs=["sofa_occ", "later"], params={"threshold": 0.5})
    later = FeatureDef(name="later", transform="any_active", inputs=["binary_sensor.sofa"],
                       info_tier=InfoTier.DISCRETE_EVENT_GATE)
    clean2, rejected2 = validate_spec(_spec(sel, base + [early, later]))
    assert "movie2" not in [f.name for f in clean2.features]


def test_mode_gates_richer_transforms_and_budget_caps():
    sel = [_sel("sensor.co2", InfoTier.CONTINUOUS_MEASUREMENT, role=Role.ENV)]
    slope = FeatureDef(name="co2_slope", transform="window_slope",
                       inputs=["sensor.co2"], info_tier=InfoTier.CONTINUOUS_MEASUREMENT)
    assert not validate_spec(_spec(sel, [slope]), mode="conservative")[0].features
    assert validate_spec(_spec(sel, [slope]), mode="full")[0].features

    # budget cap keeps only the first N
    feats = [FeatureDef(name=f"co2_{i}", transform="window_mean", inputs=["sensor.co2"],
                        info_tier=InfoTier.CONTINUOUS_MEASUREMENT) for i in range(5)]
    clean, rejected = validate_spec(_spec(sel, feats), max_features=2)
    assert len(clean.features) == 2 and any(r[1] == "over feature budget" for r in rejected)


def test_duplicate_names_rejected():
    sel = [_sel("sensor.co2", InfoTier.CONTINUOUS_MEASUREMENT, role=Role.ENV)]
    f1 = FeatureDef(name="dup", transform="window_mean", inputs=["sensor.co2"],
                    info_tier=InfoTier.CONTINUOUS_MEASUREMENT)
    f2 = FeatureDef(name="dup", transform="window_max", inputs=["sensor.co2"],
                    info_tier=InfoTier.CONTINUOUS_MEASUREMENT)
    clean, rejected = validate_spec(_spec(sel, [f1, f2]))
    assert len(clean.features) == 1 and rejected[0][1] == "duplicate feature name"

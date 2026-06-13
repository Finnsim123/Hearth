"""Spec-driven feature builder — deterministic execution of a FeatureSpec."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from hearth.domain.features.spec_builder import build_features_from_spec
from hearth.domain.features.validate import validate_spec
from hearth.domain.schemas import (
    EntitySelection, FeatureDef, FeatureSpec, InfoTier, Role,
)

WINDOW = timedelta(minutes=30)


def _prepared():
    """One hour on a 1-min grid: sofa occupied the first 15 min, CO2 ramps
    600..659 (1/min), media 'playing' first 10 min then 'idle'."""
    idx = pd.date_range("2026-06-01 00:00", periods=60, freq="1min", tz="UTC")
    sofa = np.where(np.arange(60) < 15, 1.0, 0.0)
    co2 = 600.0 + np.arange(60)
    media = np.where(np.arange(60) < 10, "playing", "idle")
    return pd.DataFrame({"sofa": sofa, "co2": co2, "tv": media}, index=idx)


E2C = {"binary_sensor.sofa": "sofa", "sensor.co2": "co2", "media_player.tv": "tv"}
GRID = [pd.Timestamp("2026-06-01 00:00", tz="UTC").to_pydatetime()]   # window 00:00–00:30


def _build(features, selections):
    spec = FeatureSpec(selections=selections, features=features)
    clean, rej = validate_spec(spec, mode="full")
    assert not rej, f"spec should validate: {rej}"
    return build_features_from_spec(_prepared(), clean, GRID, entity_to_col=E2C, window=WINDOW)


def test_numeric_and_gate_transforms():
    sel = [EntitySelection(entity_id="binary_sensor.sofa", keep=True, role=Role.PRESENCE,
                           info_tier=InfoTier.DISCRETE_EVENT_GATE),
           EntitySelection(entity_id="sensor.co2", keep=True, role=Role.ENV,
                           info_tier=InfoTier.CONTINUOUS_MEASUREMENT)]
    feats = [
        FeatureDef(name="sofa_occ", transform="occupancy_fraction",
                   inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="sofa_any", transform="any_active",
                   inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="sofa_run", transform="run_length_on",
                   inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        FeatureDef(name="co2_mean", transform="window_mean",
                   inputs=["sensor.co2"], info_tier=InfoTier.CONTINUOUS_MEASUREMENT),
        FeatureDef(name="co2_delta", transform="window_delta",
                   inputs=["sensor.co2"], info_tier=InfoTier.CONTINUOUS_MEASUREMENT),
        FeatureDef(name="co2_slope", transform="window_slope",
                   inputs=["sensor.co2"], info_tier=InfoTier.CONTINUOUS_MEASUREMENT),
    ]
    df, skipped = _build(feats, sel)
    assert not skipped
    r = df.iloc[0]
    assert r["sofa_occ"] == 0.5            # 15 of 30 min
    assert r["sofa_any"] == 1.0
    assert r["sofa_run"] == 15.0           # 15 contiguous on-minutes
    assert r["co2_mean"] == 614.5          # mean(600..629)
    assert r["co2_delta"] == 29.0          # 629 - 600
    assert abs(r["co2_slope"] - 1.0) < 1e-6   # +1/min ramp


def test_counter_rate_and_dwell_and_transitions():
    idx = pd.date_range("2026-06-01 00:00", periods=60, freq="1min", tz="UTC")
    prepared = pd.DataFrame({
        "kwh": 100.0 + np.arange(60) * 0.5,           # +0.5 kWh/min
        "tv": np.where(np.arange(60) < 10, "playing", "idle"),
    }, index=idx)
    e2c = {"sensor.kwh": "kwh", "media_player.tv": "tv"}
    sel = [EntitySelection(entity_id="sensor.kwh", keep=True, role=Role.POWER,
                           info_tier=InfoTier.CUMULATIVE_COUNTER),
           EntitySelection(entity_id="media_player.tv", keep=True, role=Role.MEDIA,
                           info_tier=InfoTier.STATE_MACHINE)]
    feats = [
        FeatureDef(name="kwh_rate", transform="counter_rate", inputs=["sensor.kwh"],
                   info_tier=InfoTier.CUMULATIVE_COUNTER),
        FeatureDef(name="tv_playing_frac", transform="state_dwell_fraction",
                   inputs=["media_player.tv"], params={"state": "playing"},
                   info_tier=InfoTier.STATE_MACHINE),
        FeatureDef(name="tv_transitions", transform="transition_count",
                   inputs=["media_player.tv"], info_tier=InfoTier.STATE_MACHINE),
    ]
    spec = FeatureSpec(selections=sel, features=feats)
    clean, rej = validate_spec(spec, mode="full")
    assert not rej
    df, skipped = build_features_from_spec(prepared, clean, GRID, entity_to_col=e2c, window=WINDOW)
    r = df.iloc[0]
    assert abs(r["kwh_rate"] - 0.5) < 1e-9          # 0.5 kWh per minute
    assert abs(r["tv_playing_frac"] - (10 / 30)) < 1e-9
    assert r["tv_transitions"] == 1.0               # playing -> idle once


def test_composites_read_earlier_features():
    sel = [EntitySelection(entity_id="binary_sensor.sofa", keep=True, role=Role.PRESENCE,
                           info_tier=InfoTier.DISCRETE_EVENT_GATE),
           EntitySelection(entity_id="sensor.co2", keep=True, role=Role.ENV,
                           info_tier=InfoTier.CONTINUOUS_MEASUREMENT)]
    feats = [
        FeatureDef(name="sofa_occ", transform="occupancy_fraction",
                   inputs=["binary_sensor.sofa"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        # 0.5 >= 0.4 -> 1.0 ; 0.5 >= 0.6 -> 0.0
        FeatureDef(name="and_lo", transform="co_occurrence_and",
                   inputs=["sofa_occ"], params={"threshold": 0.4}),
        FeatureDef(name="and_hi", transform="co_occurrence_and",
                   inputs=["sofa_occ"], params={"threshold": 0.6}),
        FeatureDef(name="absent", transform="absence_and", inputs=["sofa_occ"]),
    ]
    df, skipped = _build(feats, sel)
    r = df.iloc[0]
    assert r["and_lo"] == 1.0 and r["and_hi"] == 0.0
    assert r["absent"] == 0.0                        # sofa_occ 0.5 is not < 0.5


def test_missing_column_is_zero_and_unimplemented_is_skipped():
    sel = [EntitySelection(entity_id="binary_sensor.ghost", keep=True, role=Role.PRESENCE,
                           info_tier=InfoTier.DISCRETE_EVENT_GATE),
           EntitySelection(entity_id="media_player.tv", keep=True, role=Role.MEDIA,
                           info_tier=InfoTier.STATE_MACHINE)]
    feats = [
        # entity has no column in `prepared` -> 0.0, no crash
        FeatureDef(name="ghost_occ", transform="occupancy_fraction",
                   inputs=["binary_sensor.ghost"], info_tier=InfoTier.DISCRETE_EVENT_GATE),
        # state_onehot has no executor yet -> recorded as skipped, no column
        FeatureDef(name="tv_oh", transform="state_onehot", inputs=["media_player.tv"],
                   params={"states": ["playing", "idle"]}, info_tier=InfoTier.STATE_MACHINE),
    ]
    spec = FeatureSpec(selections=sel, features=feats)
    clean, rej = validate_spec(spec, mode="full")
    assert not rej
    df, skipped = build_features_from_spec(_prepared(), clean, GRID, entity_to_col={}, window=WINDOW)
    assert df.iloc[0]["ghost_occ"] == 0.0
    assert "tv_oh" not in df.columns
    assert skipped and skipped[0][0] == "tv_oh"

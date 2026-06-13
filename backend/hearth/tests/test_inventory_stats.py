"""Entity-catalog aggregate statistics (llm_layer_design §a) — pure, privacy-safe."""
from __future__ import annotations

import numpy as np
import pandas as pd

import pytest

from hearth.domain.onboarding.inventory import (
    build_catalog, catalog_record, entity_stats, set_stats_consent,
    stats_consent, stats_consent_decided, value_type_of,
)


class _Repo:
    def __init__(self):
        self.s: dict = {}
    def get_setting(self, k, d=None):
        return self.s.get(k, d)
    def set_setting(self, k, v):
        self.s[k] = v


def _series(values, start="2026-06-01 00:00", freq="1min"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx)


def test_value_type_classification():
    assert value_type_of(_series([0, 1, 0, 1, 1, 0])) == "boolean"
    assert value_type_of(_series(np.linspace(600, 660, 60))) == "numeric_continuous"
    assert value_type_of(_series([1, 2, 3, 2, 1])) == "numeric_discrete"
    assert value_type_of(_series(["playing", "idle", "paused", "idle"])) == "enum"
    assert value_type_of(_series([f"v{i}" for i in range(40)])) == "string"
    assert value_type_of(pd.Series(dtype=object)) == "unknown"


def test_numeric_ramp_stats():
    s = _series(np.arange(600.0, 660.0))          # +1/min, strictly increasing
    end = s.index[-1] + pd.Timedelta(minutes=1)
    st = entity_stats(s, days=1, end=end)
    assert st["value_type"] == "numeric_continuous"
    assert st["numeric"]["min"] == 600.0 and st["numeric"]["max"] == 659.0
    assert st["numeric"]["monotonic_increasing_frac"] == 1.0   # counter signature
    assert st["top_states"] is None
    assert abs(sum(st["active_hours_hist"]) - 1.0) < 1e-6


def test_flatlined_sensor_is_stuck():
    st = entity_stats(_series([0.2] * 120), days=1)
    assert st["distinct_values"] == 1
    assert st["flatline_frac"] == 1.0              # never moved = suspect
    assert st["changes_per_day"] == 0.0


def test_enum_top_states_and_changes():
    s = _series(["playing"] * 10 + ["idle"] * 20)
    st = entity_stats(s, days=1)
    assert st["value_type"] == "enum"
    assert st["numeric"] is None
    tops = {d["value"]: d["frac"] for d in st["top_states"]}   # fracs rounded to 4dp
    assert abs(tops["idle"] - (20 / 30)) < 1e-3 and abs(tops["playing"] - (10 / 30)) < 1e-3
    assert st["changes_per_day"] == 1.0            # one playing->idle change


def test_gap_and_staleness():
    # two clusters of changes with a long gap, then silence to `end`
    idx = pd.DatetimeIndex([
        pd.Timestamp("2026-06-01 00:00", tz="UTC"),
        pd.Timestamp("2026-06-01 00:05", tz="UTC"),
        pd.Timestamp("2026-06-01 06:00", tz="UTC"),   # ~6h gap before this
    ])
    s = pd.Series([0.0, 1.0, 0.0], index=idx)
    end = pd.Timestamp("2026-06-01 12:00", tz="UTC")  # 6h since last change
    st = entity_stats(s, days=1, end=end)
    assert st["longest_gap_hours"] >= 5.9
    assert 5.9 <= st["last_changed_age_hours"] <= 6.1


def test_empty_series():
    st = entity_stats(pd.Series(dtype=object), days=7)
    assert st["value_type"] == "unknown" and st["distinct_values"] == 0
    assert st["numeric"] is None and st["top_states"] is None


def test_stats_consent_toggle():
    r = _Repo()
    assert stats_consent(r) is False and stats_consent_decided(r) is False   # undecided -> NO
    assert set_stats_consent(r, "yes") is True
    assert stats_consent(r) is True and stats_consent_decided(r) is True
    assert set_stats_consent(r, False) is False
    assert stats_consent(r) is False and stats_consent_decided(r) is True     # decided NO
    with pytest.raises(ValueError):
        set_stats_consent(r, "maybe")


def test_catalog_record_gates_stats_on_consent():
    meta = {"entity_id": "binary_sensor.sofa", "domain": "binary_sensor",
            "friendly_name": "Sofa", "device_class": "occupancy",
            "state_class": None, "unit": None, "area": "Living room",
            "entity_category": None, "disabled": False}
    s = _series([0, 1, 0, 1])

    # metadata is always present; stats/samples withheld without consent
    no = catalog_record(meta, series=s, share_stats=False)
    assert no["metadata"]["device_class"] == "occupancy"
    assert no["metadata"]["unit_of_measurement"] is None       # mapped from "unit"
    assert no["stats"] is None and no["samples"] is None

    # with consent, stats + samples are attached
    yes = catalog_record(meta, series=s, share_stats=True)
    assert yes["stats"]["value_type"] == "boolean"
    assert yes["samples"] and "state" in yes["samples"][0]


def test_build_catalog_honours_consent():
    inv = [{"entity_id": "sensor.co2", "domain": "sensor", "unit": "ppm"},
           {"entity_id": "binary_sensor.sofa", "domain": "binary_sensor"}]
    series_by = {"sensor.co2": _series(np.linspace(600, 660, 30))}
    off = build_catalog(inv, share_stats=False, series_by_entity=series_by)
    assert all(r["stats"] is None for r in off)
    on = build_catalog(inv, share_stats=True, series_by_entity=series_by)
    co2 = next(r for r in on if r["entity_id"] == "sensor.co2")
    sofa = next(r for r in on if r["entity_id"] == "binary_sensor.sofa")
    assert co2["stats"]["value_type"] == "numeric_continuous"
    assert sofa["stats"] is None                               # no history for sofa -> None

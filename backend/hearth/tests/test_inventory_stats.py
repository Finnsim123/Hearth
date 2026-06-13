"""Entity-catalog aggregate statistics (llm_layer_design §a) — pure, privacy-safe."""
from __future__ import annotations

import numpy as np
import pandas as pd

from hearth.domain.onboarding.inventory import entity_stats, value_type_of


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

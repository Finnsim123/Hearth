"""Accuracy pack: event dynamics, learned transitions, margin asking, calibration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from hearth.domain.features.pipeline import event_dynamics, extract_windows
from hearth.domain.inference.smoothing import learn_transitions, transition_filter
from hearth.domain.schemas import Binding, Role


def _bindings():
    return [Binding(entity_id="binary_sensor.kitchen", role=Role.PRESENCE, name="kitchen"),
            Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE, name="sofa"),
            Binding(entity_id="sensor.co2", role=Role.ENV, name="co2")]


def _prepared(active_minutes: set[int], n: int = 120) -> pd.DataFrame:
    start = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(minutes=i) for i in range(n)])
    kitchen = pd.Series(0.0, index=idx)
    for m in active_minutes:
        kitchen.iloc[m] = 1.0                      # rising+falling edges
    return pd.DataFrame({"kitchen": kitchen,
                         "sofa": pd.Series(0.0, index=idx),
                         "co2": pd.Series(700.0, index=idx)})


def test_event_dynamics_counts_and_idleness():
    prepared = _prepared({5, 6, 7, 40})            # burst early, one blip at :40
    grid = [datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc)]
    df = extract_windows(prepared, _bindings(), grid)
    assert df.loc[grid[0], "evt_count"] > 0        # busy window
    assert df.loc[grid[2], "evt_count"] == 0       # silent window
    # idleness GROWS across silent windows — the "nothing moved for N min" clock
    assert df.loc[grid[2], "evt_idle_minutes"] > df.loc[grid[1], "evt_idle_minutes"] > 0
    # env churn (co2) is NOT an event: dynamics ignore ambient roles
    assert df.loc[grid[2], "evt_active_sensors"] == 0


def test_event_dynamics_none_without_event_sensors():
    prepared = _prepared(set())[["co2"]]
    assert event_dynamics(prepared, [_bindings()[2]]) is None


def test_learned_transitions_sticky_and_filter():
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(minutes=30 * i) for i in range(200)])
    # 0-100 sleeping, 100-200 home: transitions are overwhelmingly self→self
    labels = pd.Series(["sleeping"] * 100 + ["home"] * 100, index=idx)
    trans = learn_transitions(labels)
    assert trans["sleeping"]["sleeping"] > 0.9
    assert trans["sleeping"]["home"] < 0.1
    # forward filter: a noisy 55/45 flip toward "home" at a sleeping moment
    # is suppressed by the sticky prior…
    row = pd.Series({"sleeping": 0.45, "home": 0.55})
    filtered = transition_filter(row, "sleeping", trans)
    assert filtered["sleeping"] > filtered["home"]
    assert abs(filtered.sum() - 1.0) < 1e-9
    # …but a DECISIVE observation still wins (uniform mix = escape hatch)
    decisive = pd.Series({"sleeping": 0.02, "home": 0.98})
    assert transition_filter(decisive, "sleeping", trans)["home"] > 0.5


def test_transition_filter_noop_without_model():
    row = pd.Series({"a": 0.6, "b": 0.4})
    assert transition_filter(row, None, None).equals(row)
    assert transition_filter(row, "unknown", {"a": {"a": 1.0}}).equals(row)


def test_calibration_fixes_overconfidence():
    from hearth.domain.training.estimators import RandomForestEstimator
    rng = np.random.default_rng(3)
    n = 600
    X = pd.DataFrame({"x1": rng.normal(0, 1, n), "x2": rng.normal(0, 1, n)})
    # noisy boundary → forest tends to be overconfident on val
    y = pd.Series(np.where(X["x1"] + rng.normal(0, 1.2, n) > 0, "a", "b"))
    est = RandomForestEstimator(n_estimators=60)
    est.fit(X.iloc[:400], y.iloc[:400])
    est.calibrate(X.iloc[400:], y.iloc[400:])
    assert est.calibrators                          # fitted
    probs = est.predict_proba(X.iloc[400:])
    assert np.allclose(probs.sum(axis=1), 1.0)      # still a distribution
    # calibrated confidence should track empirical accuracy within ~12pts
    conf = probs.max(axis=1)
    acc = (probs.idxmax(axis=1) == y.iloc[400:]).mean()
    assert abs(conf.mean() - acc) < 0.12

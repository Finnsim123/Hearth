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


def test_daypart_transitions_are_time_conditioned():
    """sleeping→cooking happens only in the morning, so the morning matrix must
    allow it while the night matrix suppresses it (audit F6)."""
    from hearth.domain.inference.smoothing import learn_transitions_by_daypart
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(minutes=30 * i) for i in range(480)])
    # every morning (hour 8) a sleeping→cooking flip; nights stay sleeping
    labels = []
    for ts in idx:
        labels.append("cooking" if ts.hour == 8 else "sleeping")
    s = pd.Series(labels, index=idx)
    dp = learn_transitions_by_daypart(s, tz="UTC")
    assert "all" in dp and "1" in dp                     # morning bucket = 1
    # morning matrix: sleeping→cooking is real; night matrix: ~never
    assert dp["1"]["sleeping"]["cooking"] > dp["0"]["sleeping"]["cooking"]
    # filter picks the matrix by daypart
    row = pd.Series({"sleeping": 0.5, "cooking": 0.5})
    morning = transition_filter(row, "sleeping", dp, daypart=1)
    night = transition_filter(row, "sleeping", dp, daypart=0)
    assert morning["cooking"] > night["cooking"]


def test_viterbi_smooths_a_single_flicker():
    """One noisy mid-run flip is corrected by the global path when the
    transition prior is sticky (audit F6 offline relabeling)."""
    from hearth.domain.inference.smoothing import viterbi, viterbi_relabel
    trans = {"home": {"home": 0.95, "away": 0.05},
             "away": {"home": 0.05, "away": 0.95}}
    em = [{"home": 0.9, "away": 0.1}] * 5
    em[2] = {"home": 0.45, "away": 0.55}                 # lone flicker to away
    path = viterbi(em, trans)
    assert path == ["home"] * 5                          # flicker erased
    idx = pd.date_range("2026-06-01", periods=5, freq="30min", tz="UTC")
    probs = pd.DataFrame(em, index=idx)
    out = viterbi_relabel(probs, trans)
    assert list(out) == ["home"] * 5 and out.index.equals(idx)


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


def test_bootstrap_basis_names_the_fired_rule():
    from hearth.domain.labeling.rules import bootstrap_labels, predicate_text
    from hearth.domain.schemas import Rule
    idx = pd.DatetimeIndex([datetime(2026, 6, 1, h, 0, tzinfo=timezone.utc)
                            for h in (10, 23)])
    feats = pd.DataFrame({"bed_max": [0.0, 2.0], "hour_of_day": [10, 23]}, index=idx)
    rules = [Rule(activity_slug="sleeping", priority=10,
                  predicate={"all": [{"feat": "bed_max", "op": ">", "value": 1}]})]
    labels, basis = bootstrap_labels(rules, feats, "alice", "home", return_basis=True)
    assert labels.tolist() == ["home", "sleeping"]
    assert pd.isna(basis.iloc[0])                      # default — no rule fired
    assert basis.iloc[1] == "bed_max > 1"
    assert predicate_text({"any": [{"feat": "a", "op": "==", "value": 1}]}) == "(a == 1)"


def test_flux_tag_escapes_injection():
    from hearth.adapters.influx_store import _flux_tag
    assert _flux_tag("alice") == "alice"
    assert _flux_tag('x" or true or "') == 'x\\" or true or \\"'
    assert _flux_tag("a\\b") == "a\\\\b"


def test_time_granularity_changes_columns_and_hash():
    from datetime import datetime, timezone
    from hearth.domain.features.pipeline import extract_windows
    from hearth.domain.features.registry import feature_set_version
    grid = [datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc),   # night
            datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)]    # morning
    empty = pd.DataFrame()
    full = extract_windows(empty, [], grid, "UTC", "full")
    coarse = extract_windows(empty, [], grid, "UTC", "coarse")
    none = extract_windows(empty, [], grid, "UTC", "none")
    assert "hour_of_day" in full.columns and "time_bucket" not in full.columns
    assert "time_bucket" in coarse.columns and "hour_of_day" not in coarse.columns
    assert coarse["time_bucket"].tolist() == [0.0, 1.0]         # night, morning
    assert not any(c in none.columns for c in ("hour_of_day", "time_bucket", "is_weekend"))
    # each granularity yields a DISTINCT feature-set hash (clean retrain)
    hashes = {feature_set_version([], g) for g in ("full", "coarse", "none")}
    assert len(hashes) == 3

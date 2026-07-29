"""Duration-aware smoothing: run histograms, hazards, and the decaying diagonal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from hearth.domain.inference.smoothing import (duration_adjusted_row,
                                               duration_hazard,
                                               learn_durations,
                                               transition_filter)

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _series(spec: list[tuple[str, int]], gap_after: int | None = None):
    """Label series from (label, n_windows) runs on a 30-min grid; optional
    1-h hole after `gap_after` windows."""
    labels, times, t = [], [], T0
    n = 0
    for lab, cnt in spec:
        for _ in range(cnt):
            labels.append(lab)
            times.append(t)
            n += 1
            t += timedelta(minutes=30)
            if gap_after is not None and n == gap_after:
                t += timedelta(hours=1)
    return pd.Series(labels, index=pd.DatetimeIndex(times))


def test_learn_durations_counts_completed_runs_only():
    s = _series([("sleep", 16), ("cook", 2), ("sleep", 14), ("cook", 3)])
    hist = learn_durations(s)
    assert hist["sleep"] == {"16": 1, "14": 1}   # both sleep runs end observed
    assert hist["cook"] == {"2": 1}              # final cook run: censored


def test_gap_censors_the_open_run():
    s = _series([("tv", 4), ("tv", 4), ("cook", 2)], gap_after=4)
    hist = learn_durations(s)
    # first tv run hits the gap -> censored; second ends observed at cook
    assert hist.get("tv") == {"4": 1}


def test_hazard_grows_as_run_outlives_typical():
    hist = {"2": 6, "3": 2}                    # 8 cook runs: most last 1 h
    early = duration_hazard(hist, 1)           # nobody ends this early
    typical = duration_hazard(hist, 2)         # most runs end here
    late = duration_hazard(hist, 4)            # longer than any observed run
    assert early < typical
    assert typical > 0.5
    assert late == 0.5                         # beyond-longest default pressure


def test_hazard_needs_enough_runs():
    assert duration_hazard({"2": 2}, 2) is None    # < MIN_RUNS
    assert duration_hazard({}, 1) is None


def test_adjusted_row_moves_mass_off_the_diagonal():
    row = {"cook": 0.8, "eat": 0.15, "away": 0.05}
    adj = duration_adjusted_row(row, "cook", hazard=0.75)
    assert abs(sum(adj.values()) - 1.0) < 1e-9
    assert adj["cook"] == 0.25                 # 1 - hazard
    assert adj["eat"] > adj["away"]            # off-diagonal shape preserved


def test_filter_end_to_end_blip_dies_marathon_ends():
    trans = {"cook": {"cook": 0.9, "eat": 0.1}, "eat": {"cook": 0.1, "eat": 0.9}}
    durations = {"cook": {"2": 8}}             # cooking always lasts ~1 h here
    probs = pd.Series({"cook": 0.45, "eat": 0.55})   # classifier leans away
    # early in the run: sticky prior holds the line against a weak challenger
    early = transition_filter(probs, "cook", trans, durations=durations, run_len=1)
    assert early.idxmax() == "cook"
    # run has outlived every observed cook: the prior lets go
    late = transition_filter(probs, "cook", trans, durations=durations, run_len=5)
    assert late.idxmax() == "eat"
    # no duration data at all -> unchanged stationary behaviour
    plain = transition_filter(probs, "cook", trans)
    assert plain.idxmax() == "cook"

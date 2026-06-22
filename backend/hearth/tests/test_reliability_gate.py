from __future__ import annotations

import numpy as np
import pandas as pd

from hearth.domain.foundational.reliability import (
    FACT_THRESHOLD,
    PRESENCE,
    SLEEP,
    label_agreement,
    score_foundational,
)


def _idx(days: int, freq: str = "30min"):
    return pd.date_range("2026-06-01 00:00", periods=days * (1440 // _step(freq)),
                         freq=freq, tz="UTC")


def _step(freq: str) -> int:
    return 30 if freq == "30min" else 60


def test_good_sleep_sensor_becomes_fact():
    idx = _idx(7)
    asleep = ((idx.hour >= 23) | (idx.hour < 7)).astype(float)
    fact = pd.Series(asleep, index=idx)
    contra = pd.Series(False, index=idx)
    v = score_foundational(fact, SLEEP, contradiction=contra)
    assert v.role_decision == "fact"
    assert v.score >= FACT_THRESHOLD
    assert v.eligible
    assert v.checks["plausibility"]["median_block_min"] >= 180


def test_flaky_bed_sensor_demoted_to_feature():
    idx = _idx(7)
    # toggles every sample (≈48 flips/day) and asserts 'asleep' during the day
    flip = np.tile([1.0, 0.0], len(idx) // 2 + 1)[: len(idx)]
    fact = pd.Series(flip, index=idx)
    daytime = (idx.hour >= 8) & (idx.hour < 22)
    contra = pd.Series(daytime & (flip > 0), index=idx)
    v = score_foundational(fact, SLEEP, contradiction=contra)
    assert v.role_decision != "fact"           # not trustworthy as a fact
    assert v.score < FACT_THRESHOLD
    assert v.checks["plausibility"]["flips_per_day"] > SLEEP.max_flips_per_day
    assert v.checks["corroboration"]["contradiction_rate"] > 0.2


def test_stuck_sensor_is_suspect():
    idx = _idx(7)
    fact = pd.Series(1.0, index=idx)           # always 'in bed' → no information
    v = score_foundational(fact, SLEEP)
    assert v.role_decision == "suspect"
    assert v.checks["plausibility"]["stuck"] is True


def test_unwatched_sensor_is_held_as_feature():
    idx = _idx(2)                               # < SLEEP.min_observation_days (5)
    asleep = ((idx.hour >= 23) | (idx.hour < 7)).astype(float)
    v = score_foundational(pd.Series(asleep, index=idx), SLEEP)
    assert v.eligible is False
    assert v.role_decision == "feature"
    assert "watching" in v.reason


def test_presence_away_is_fact_quickly():
    idx = _idx(4)
    away = ((idx.weekday < 5) & (idx.hour >= 9) & (idx.hour < 17)).astype(float)
    v = score_foundational(pd.Series(away, index=idx), PRESENCE)
    assert v.role_decision == "fact"           # default-eligible + plausible
    assert v.eligible


def test_frozen_tracker_is_suspect():
    idx = _idx(4)
    v = score_foundational(pd.Series(0.0, index=idx), PRESENCE)  # never 'away'
    assert v.role_decision == "suspect"


def test_label_agreement_metrics():
    idx = pd.date_range("2026-06-01", periods=10, freq="h", tz="UTC")
    fact = pd.Series([1, 1, 0, 0, 1, 1, 0, 0, 1, 0], index=idx, dtype=float)
    truth = pd.Series([1, 1, 0, 0, 1, 0, 0, 0, 1, 1], index=idx, dtype=float)
    la = label_agreement(fact, truth)
    assert la["precision"] == round(4 / 5, 3)   # 5 asserted, 4 correct
    assert la["recall"] == round(4 / 5, 3)      # 5 true, 4 caught

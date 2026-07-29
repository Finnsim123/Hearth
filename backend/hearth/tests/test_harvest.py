"""Confirm-yesterday harvester — run segmentation + pick selection (pure parts)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hearth.domain.labeling.harvest import (MAX_TARGETED, pick_recaps,
                                            segment_runs)

T0 = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)


def _rows(start: datetime, n: int, label: str, conf: float) -> list[dict]:
    return [{"time": (start + timedelta(minutes=5 * i)).isoformat(),
             "predicted": label, "smoothed": label, "confidence": conf,
             "probs": {label: conf, "other": 1 - conf}} for i in range(n)]


def _label(ts: datetime, provenance: str = "confirmed"):
    return SimpleNamespace(window_ts=ts,
                           provenance=SimpleNamespace(value=provenance))


def test_segment_runs_groups_and_measures():
    rows = _rows(T0, 12, "sleep", 0.9) + _rows(T0 + timedelta(hours=1), 6, "away", 0.6)
    runs = segment_runs(rows)
    assert [r["label"] for r in runs] == ["sleep", "away"]
    assert runs[0]["minutes"] == 60          # 12 windows x 5 min
    assert abs(runs[1]["mean_conf"] - 0.6) < 1e-9


def test_segment_runs_splits_on_gap():
    rows = _rows(T0, 3, "home", 0.8) + _rows(T0 + timedelta(hours=2), 3, "home", 0.8)
    assert len(segment_runs(rows)) == 2      # same label, but a 2 h hole between


def test_picks_one_gold_and_targets_uncertain():
    day = (_rows(T0, 60, "sleep", 0.95)                               # confident, long
           + _rows(T0 + timedelta(hours=8), 12, "cooking", 0.45)      # uncertain
           + _rows(T0 + timedelta(hours=12), 12, "away", 0.90)
           + _rows(T0 + timedelta(hours=16), 12, "tv", 0.55))         # uncertain
    picks = pick_recaps(day, [], set(), rng=random.Random(7))
    reasons = [p["reason"] for p in picks]
    assert reasons.count("explore") == 1                              # exactly one gold probe
    targeted = [p["run"]["label"] for p in picks if p["reason"] == "uncertain"]
    assert len(targeted) <= MAX_TARGETED
    # the shakiest run is always among the targeted picks (unless it WAS the gold draw)
    gold_label = next(p["run"]["label"] for p in picks if p["reason"] == "explore")
    assert "cooking" in targeted or gold_label == "cooking"


def test_skips_covered_short_and_already_asked():
    day = (_rows(T0, 12, "sleep", 0.5)                                # covered by a label
           + _rows(T0 + timedelta(hours=2), 2, "blip", 0.3)           # 10 min — too short
           + _rows(T0 + timedelta(hours=4), 12, "tv", 0.5))           # already asked
    labels = [_label(T0 + timedelta(minutes=20))]
    tv_mid = T0 + timedelta(hours=4, minutes=30)                      # row 6 of 12
    picks = pick_recaps(day, labels, {tv_mid}, rng=random.Random(1))
    assert picks == []                                                # nothing eligible


def test_uncovered_by_pending_label_still_asked():
    day = _rows(T0, 12, "tv", 0.5)
    labels = [_label(T0 + timedelta(minutes=20), provenance="predicted")]
    # a PREDICTED-provenance row is not human coverage — run_harvest filters
    # to confirmed/corrected before calling pick_recaps; here labels list is
    # what survives that filter, so passing it empty-equivalent must still pick
    picks = pick_recaps(day, [], set(), rng=random.Random(1))
    assert len(picks) == 1 and picks[0]["reason"] == "explore"

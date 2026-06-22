from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hearth.domain.behaviour.summary import summarize

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)


def _rows(spec):
    """spec: (start_min, end_min, state, model_version) → 30-min rows."""
    rows = []
    for smin, emin, state, mv in spec:
        t = smin
        while t < emin:
            rows.append({"time": (BASE + timedelta(minutes=t)).isoformat(),
                         "smoothed": state, "predicted": state, "model_version": mv})
            t += 30
    return rows


SPEC = [
    (0, 360, "asleep", "fact-v0"),     # 6h known
    (360, 480, "cooking", "m-v1"),     # 2h inferred
    (480, 1020, "away", "fact-v0"),    # 9h known
    (1020, 1050, "unknown", "m-v1"),   # 30m unclassified
    (1050, 1080, "cooking", "m-v1"),   # 30m inferred
]


def test_time_budget_and_coverage():
    s = summarize("alice", _rows(SPEC), tz="UTC", now=BASE + timedelta(hours=18))
    assert s.totals["asleep"] == 360
    assert s.totals["away"] == 540
    assert s.totals["cooking"] == 150          # 120 + 30
    assert s.total_min == 1080
    assert s.classified_min == 1050
    assert s.coverage == round(1050 / 1080, 4)
    assert s.fact_min == 900                    # asleep + away are facts
    assert s.inferred_min == 150                # cooking from the model
    assert s.known_fraction == round(900 / 1050, 4)


def test_sleep_and_away_per_day_from_facts():
    s = summarize("alice", _rows(SPEC), tz="UTC", now=BASE + timedelta(hours=18))
    assert s.sleep_per_day_min["2026-06-10"] == 360
    assert s.away_per_day_min["2026-06-10"] == 540


def test_today_ribbon_merges_contiguous_runs():
    s = summarize("alice", _rows(SPEC), tz="UTC", now=BASE + timedelta(hours=18))
    acts = [(seg.activity, seg.basis) for seg in s.today]
    assert acts == [("asleep", "fact"), ("cooking", "model"), ("away", "fact"),
                    ("unknown", "unknown"), ("cooking", "model")]


def test_rule_basis_counts_as_inferred_not_fact():
    rows = _rows([(0, 60, "home", "rules-v0")])
    s = summarize("alice", rows, tz="UTC", now=BASE + timedelta(hours=1))
    assert s.fact_min == 0 and s.inferred_min == 60


def test_empty_is_safe():
    s = summarize("alice", [], tz="UTC", now=BASE)
    assert s.total_min == 0 and s.coverage == 0.0 and s.per_day == [] and s.today == []

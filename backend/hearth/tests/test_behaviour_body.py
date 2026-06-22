from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hearth.domain.behaviour.body import (
    active_sedentary_trends,
    _deltas,
    summarize_body,
)

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)   # Wednesday


def _samples(spec):
    """spec: list of (minute_offset, cumulative_value)."""
    return [(BASE + timedelta(minutes=m), v) for m, v in spec]


def test_deltas_difference_a_rising_counter():
    # counter 0 -> 100 -> 250 -> 400 at 0,30,60,90 min
    d = _deltas(_samples([(0, 0), (30, 100), (60, 250), (90, 400)]), 30)
    vals = [d[k] for k in sorted(d)]
    assert vals == [100, 150, 150]          # first window seeds prev, no delta


def test_deltas_handle_midnight_reset():
    # rises to 8000, then resets and climbs to 300 — the drop must not go negative
    d = _deltas(_samples([(0, 0), (30, 8000), (60, 50), (90, 300)]), 30)
    vals = [d[k] for k in sorted(d)]
    assert vals[0] == 8000                   # 0 -> 8000
    assert vals[1] == 50                     # reset: counts the post-reset value
    assert vals[2] == 250                    # 50 -> 300 normal diff
    assert all(v >= 0 for v in vals)


def test_absent_windows_are_not_counted_as_zero():
    # only two samples 6h apart → only one diff window exists; the gap is absent,
    # NOT a run of zero-activity windows.
    d = _deltas(_samples([(0, 0), (360, 500)]), 30)
    assert list(d.values()) == [500]
    assert len(d) == 1


def test_summary_totals_rhythm_and_coverage():
    counters = {"steps": _samples([(0, 0), (30, 100), (60, 300), (90, 600)])}
    s = summarize_body("alice", counters, [], tz="UTC",
                       now=BASE + timedelta(minutes=120), range_start=BASE)
    assert s.primary == "steps"
    assert s.units["steps"] == "steps"
    assert s.total["steps"] == 600           # 100 + 200 + 300 (3 diff windows)
    # rhythm cells on Wednesday (weekday 2), hours 0 and 1
    cells = {(c.dow, c.hour): c.totals["steps"] for c in s.rhythm}
    assert cells[(2, 0)] == 100              # window starting 00:30
    assert cells[(2, 1)] == 200 + 300        # windows at 01:00 and 01:30
    # coverage: 3 worn windows out of 4 (00:00 seeds, no delta) over a 2h span
    assert 0 < s.coverage <= 1


def test_cross_tab_attributes_steps_to_the_activity():
    counters = {"steps": _samples([(0, 0), (30, 100), (60, 700)])}
    rows = [
        {"time": (BASE + timedelta(minutes=30)).isoformat(), "smoothed": "cooking",
         "model_version": "m-v1"},
        {"time": (BASE + timedelta(minutes=60)).isoformat(), "smoothed": "cleaning",
         "model_version": "m-v1"},
    ]
    s = summarize_body("alice", counters, rows, tz="UTC",
                       now=BASE + timedelta(minutes=90), range_start=BASE)
    assert s.by_activity["cooking"] == 100   # delta in the 00:30 window
    assert s.by_activity["cleaning"] == 600  # delta in the 01:00 window


def test_charging_windows_split_coverage_and_drop_from_activity():
    # steps in 4 diff windows; charging during the last two → those are "docked",
    # excluded from totals/worn, counted as charging.
    counters = {"steps": _samples([(0, 0), (30, 100), (60, 200), (90, 1000), (120, 1100)])}
    charging = [(BASE + timedelta(minutes=90), "on"), (BASE + timedelta(minutes=120), "charging")]
    s = summarize_body("alice", counters, [], tz="UTC",
                       now=BASE + timedelta(minutes=150), range_start=BASE, charging=charging)
    assert s.total["steps"] == 200          # only the two non-charging windows
    assert s.worn_min == 60                  # 2 worn windows
    assert s.charging_min == 60              # 2 docked windows
    assert s.absent_min == 30                # 1 window with no data
    assert s.coverage == round(2 / 5, 4)


def test_active_vs_sedentary_split():
    # window deltas: 10 (sedentary), 500 (active), 5 (sedentary) → 1 active / 2 sed
    counters = {"steps": _samples([(0, 0), (30, 10), (60, 510), (90, 515)])}
    s = summarize_body("alice", counters, [], tz="UTC",
                       now=BASE + timedelta(minutes=120), range_start=BASE)
    assert s.active_min == 30
    assert s.sedentary_min == 60


def test_active_trend_flags_a_rise():
    # active windows every day, more in the recent week than the prior week
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    samples = []
    for d in range(0, 7):                       # recent: 2 active windows/day
        base = now - timedelta(days=d)
        samples += [(base, 0.0), (base + timedelta(minutes=30), 600.0),
                    (base + timedelta(minutes=60), 1200.0)]
    for d in range(8, 15):                      # prior: 0 active (flat)
        base = now - timedelta(days=d)
        samples += [(base, 0.0), (base + timedelta(minutes=30), 5.0)]
    cs = {c.activity: c for c in active_sedentary_trends({"steps": samples}, now=now)}
    assert "active" in cs and cs["active"].direction in ("up", "new")


def test_empty_is_safe():
    s = summarize_body("alice", {}, [], tz="UTC", now=BASE, range_start=BASE)
    assert s.signals == [] and s.primary is None and s.per_day == []
    assert s.coverage == 0.0 and s.by_activity == {}
    assert s.worn_min == 0 and s.charging_min == 0 and s.absent_min == 0
    assert s.active_min == 0 and s.sedentary_min == 0 and s.trends == []

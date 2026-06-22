from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hearth.domain.behaviour.summary import summarize, trends

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
    assert s.rhythm == [] and s.sequences == []


def test_rhythm_grid_bins_by_local_dow_and_hour():
    # BASE is 2026-06-10, a Wednesday (weekday()==2), 00:00 UTC.
    s = summarize("alice", _rows(SPEC), tz="UTC", now=BASE + timedelta(hours=18))
    cells = {(c.dow, c.hour): c.totals for c in s.rhythm}
    # asleep ran 00:00–06:00 → hours 0..5 on Wednesday, 30+30 min each hour
    assert cells[(2, 0)]["asleep"] == 60
    assert cells[(2, 5)]["asleep"] == 60
    # cooking 06:00–08:00 → hours 6,7
    assert cells[(2, 6)]["cooking"] == 60
    # unknown windows never appear in the rhythm grid
    assert all("unknown" not in t for t in cells.values())


NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


def _span(day_from, day_to, mins_per_day, state, mv):
    """30-min rows at 08:00 local, for days [day_from, day_to) back from NOW."""
    rows = []
    for d in range(day_from, day_to):
        base = NOW - timedelta(days=d)
        for k in range(int(mins_per_day // 30)):
            rows.append({"time": (base + timedelta(minutes=30 * k)).isoformat(),
                         "smoothed": state, "predicted": state, "model_version": mv})
    return rows


def test_trends_flags_notable_changes_only():
    rows = (
        _span(0, 7, 90, "cooking", "m-v1") + _span(8, 15, 30, "cooking", "m-v1")   # up
        + _span(0, 7, 40, "reading", "m-v1")                                       # new
        + _span(0, 7, 420, "asleep", "fact-v0") + _span(8, 15, 420, "asleep", "fact-v0")  # stable
    )
    cs = {c.activity: c for c in trends("alice", rows, tz="UTC", now=NOW)}
    assert cs["cooking"].direction == "up"
    assert cs["cooking"].delta_min == 60.0
    assert cs["cooking"].basis == "inferred"
    assert cs["reading"].direction == "new"
    assert "asleep" not in cs                       # stable → not surfaced


def test_trends_basis_marks_facts():
    rows = _span(0, 7, 600, "away", "fact-v0")      # away appears only recently
    cs = {c.activity: c for c in trends("alice", rows, tz="UTC", now=NOW)}
    assert cs["away"].direction == "new" and cs["away"].basis == "fact"


def test_sequences_are_change_transitions_excluding_self_loops():
    s = summarize("alice", _rows(SPEC), tz="UTC", now=BASE + timedelta(hours=18))
    pairs = {(t.src, t.dst): t for t in s.sequences}
    # observed changes: asleep→cooking, cooking→away. The unknown window breaks
    # the chain, so away→cooking (across the gap) is NOT counted, and there are
    # no self-loops (asleep→asleep etc.).
    assert ("asleep", "cooking") in pairs
    assert ("cooking", "away") in pairs
    assert ("away", "cooking") not in pairs
    assert all(t.src != t.dst for t in s.sequences)
    # cooking only ever led to away here → prob 1.0
    assert pairs[("cooking", "away")].prob == 1.0

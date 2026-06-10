from __future__ import annotations

from datetime import datetime, timezone

from hearth.domain.labeling.bulk import bulk_label_events, windows_in_range


def test_windows_aligned_and_contained():
    start = datetime(2026, 6, 1, 19, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc)
    ws = windows_in_range(start, end)
    assert [w.strftime("%H:%M") for w in ws] == ["19:30", "20:00", "20:30"]


def test_bulk_events_confirmed_with_bulk_source():
    evs = bulk_label_events("alice", datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
                            datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc), "movie")
    assert len(evs) == 4
    assert all(e.provenance.value == "confirmed" and e.source == "bulk"
               and e.label == "movie" for e in evs)


def test_empty_range():
    t = datetime(2026, 6, 1, 19, 10, tzinfo=timezone.utc)
    assert windows_in_range(t, t) == []

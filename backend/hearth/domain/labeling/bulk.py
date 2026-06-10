"""Bulk labeling — "yesterday 19–21h was movie" -> confirmed labels per window.

The single highest-leverage feedback gesture: one action labels many windows.
Bulk labels are confirmed provenance but flagged source='bulk' so future
analysis can down-weight them if recall bias shows up (RESEARCH.md open Q).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..schemas import LabelEvent, Provenance

WINDOW_MIN = 30


def windows_in_range(start: datetime, end: datetime) -> list[datetime]:
    """All 30-min window starts fully inside [start, end)."""
    first = start - timedelta(minutes=start.minute % WINDOW_MIN,
                              seconds=start.second, microseconds=start.microsecond)
    if first < start:
        first += timedelta(minutes=WINDOW_MIN)
    out, t = [], first
    while t + timedelta(minutes=WINDOW_MIN) <= end:
        out.append(t)
        t += timedelta(minutes=WINDOW_MIN)
    return out


def bulk_label_events(person_id: str, start: datetime, end: datetime,
                      activity_slug: str, source: str = "bulk") -> list[LabelEvent]:
    return [LabelEvent(person_id=person_id, window_ts=ts, label=activity_slug,
                       provenance=Provenance.CONFIRMED, source=source)
            for ts in windows_in_range(start, end)]

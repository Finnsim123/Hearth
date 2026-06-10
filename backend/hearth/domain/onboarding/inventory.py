"""Entity inventory builder — the onboarding assistant's input artifact.

Builds one JSON document describing everything a home has (DATA_MODEL.md §4):
  - GET /api/states                      every entity + attributes
  - WS config/entity_registry/list      device_class, area, device, disabled
  - WS config/area_registry/list        room candidates
  - + aggregate stats per entity over the last `stats_days` when a history
    source exists (HA recorder or an existing HA->Influx bucket)

Privacy contract: this document (metadata + aggregates) is the ONLY thing
ever shared with an LLM. Raw time series never leave the stack. The wizard
renders it and offers a download button.
"""
from __future__ import annotations

from ..ports import TimeSeriesStore


async def build_inventory(events, tsdb: TimeSeriesStore | None, stats_days: int = 14) -> list[dict]:
    """events: HaWebSocketSource (EventSource + discover_entities()).
    Returns the inventory list; stats fields are None without history."""
    raise NotImplementedError


def entity_stats(series, days: int) -> dict:
    """distinct_values, changes_per_day, active_hours_hist[24], value_range,
    pct_missing — cheap aggregates only, never raw values."""
    raise NotImplementedError

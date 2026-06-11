"""History importer — existing HA->InfluxDB bucket into hearth_raw.

Supports HA's influxdb-integration schemas:
  default:  measurement = unit (or entity_id), tag entity_id = object_id,
            fields value (float) / state (string)
  merged:   measurement = full entity path (the prototype's layout)

For each binding we try both layouts and write whatever matches. Bulk
operation, run once from the wizard's "import history" action.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..domain.schemas import Binding, EntityState
from .influx_store import InfluxStore

log = logging.getLogger(__name__)

# Import in time slices so a multi-year backfill never holds a whole entity's
# series in memory at once — bounds peak RAM regardless of how far back history
# goes (5 years × 248 sensors is fine, one month at a time).
CHUNK_DAYS = 30


def _frames(store: InfluxStore, flux: str) -> pd.DataFrame:
    df = store.query_api.query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    return df


def earliest_source_time(store: InfluxStore, source_bucket: str) -> datetime | None:
    """The oldest timestamp anywhere in the source bucket — i.e. when this home
    started recording. Used to import the FULL history instead of a fixed
    window. One scan, run once from the wizard / fast-track."""
    flux = f'''
from(bucket: "{source_bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r._field == "value" or r._field == "state")
  |> first()
  |> group()
  |> keep(columns: ["_time"])
  |> sort(columns: ["_time"])
  |> limit(n: 1)
'''
    try:
        df = _frames(store, flux)
    except Exception as exc:
        log.warning("earliest-time probe failed for %s: %s", source_bucket, exc)
        return None
    if df.empty or "_time" not in df.columns:
        return None
    return pd.to_datetime(df["_time"].iloc[0], utc=True).to_pydatetime()


def _import_binding(store: InfluxStore, source_bucket: str, b: Binding,
                    start: datetime, end: datetime) -> int:
    object_id = b.entity_id.split(".", 1)[-1]
    candidates = [
        # merged layout: measurement == full entity id
        f'r._measurement == "{b.entity_id}"',
        # default layout: any measurement, entity_id tag == object id
        f'r["entity_id"] == "{object_id}"',
    ]
    imported = 0
    chosen: str | None = None          # lock the layout once we find data
    cursor = start
    while cursor < end:
        cstop = min(cursor + timedelta(days=CHUNK_DAYS), end)
        for cond in ([chosen] if chosen else candidates):
            flux = f'''
from(bucket: "{source_bucket}")
  |> range(start: {cursor.isoformat()}, stop: {cstop.isoformat()})
  |> filter(fn: (r) => {cond})
  |> filter(fn: (r) => r._field == "value" or r._field == "state")
  |> aggregateWindow(every: 1m, fn: last, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''
            try:
                df = _frames(store, flux)
            except Exception as exc:
                log.warning("import query failed for %s: %s", b.name, exc)
                continue
            if df.empty:
                continue
            states = [EntityState(entity_id=b.entity_id, state=v,
                                  ts=pd.to_datetime(t, utc=True).to_pydatetime())
                      for t, v in zip(df["_time"], df["_value"])]
            for i in range(0, len(states), 5000):
                store.write_raw(b, states[i:i + 5000])
            imported += len(states)
            chosen = cond
            break
        cursor = cstop
    if imported:
        log.info("imported %d points for %s", imported, b.name)
    return imported


def import_history(store: InfluxStore, source_bucket: str,
                   bindings: list[Binding], start: datetime, end: datetime) -> dict[str, int]:
    """Returns {binding.name: imported_points}. Imports [start, end) for every
    binding, chunked in time to bound memory on long backfills."""
    return {b.name: _import_binding(store, source_bucket, b, start, end)
            for b in bindings}

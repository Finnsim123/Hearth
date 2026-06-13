"""History importer — existing HA->InfluxDB bucket into hearth_raw.

Supports HA's influxdb-integration schemas:
  default:  measurement = unit (or entity_id), tag entity_id = object_id,
            fields value (float) / state (string)
  merged:   measurement = full entity path (the prototype's layout)

For each binding we try both layouts and write whatever matches. Bulk
operation, run once from the wizard's "import history" action.
"""
from __future__ import annotations

import asyncio
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
    # NB: range(start: 0) means "0 seconds ago" in Flux (relative duration) — an
    # empty window. Use an absolute epoch so we actually scan ALL of time.
    flux = f'''
from(bucket: "{source_bucket}")
  |> range(start: 1970-01-01T00:00:00Z)
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


# ── HA recorder warm-start ───────────────────────────────────────────────────
# Every HA install already keeps ~10 days in its recorder, exposed by the
# history API — no HA→InfluxDB integration required. Pulling that in at setup
# gives EVERY home a provisional model on day one (an external Influx bucket,
# when present, just extends the history for a stronger start). Source: HA
# `EventSource.history()` over `/api/history/period`.
RECORDER_ENTITY_BATCH = 20      # entities per history request
RECORDER_DAY_CHUNK = 2          # days per history request (bounds payload size)


async def import_recorder_history(
    events, store: InfluxStore, bindings: list[Binding],
    start: datetime, end: datetime, repo=None,
    *, entity_batch: int = RECORDER_ENTITY_BATCH, day_chunk: int = RECORDER_DAY_CHUNK,
) -> dict[str, int]:
    """Backfill [start, end) from HA's recorder via the history API into
    hearth_raw. Batched by entity and time so a wide home doesn't make one giant
    request. Returns {binding.name: imported_points}; failures of a single batch
    are logged and skipped, never fatal."""
    by_eid = {b.entity_id: b for b in bindings}
    counts: dict[str, int] = {b.name: 0 for b in bindings}
    eids = list(by_eid)
    if not eids:
        return counts
    batches = fails = 0
    cursor = start
    while cursor < end:
        cstop = min(cursor + timedelta(days=day_chunk), end)
        for i in range(0, len(eids), entity_batch):
            batch = eids[i:i + entity_batch]
            batches += 1
            try:
                states = await events.history(batch, cursor, cstop)
            except Exception as exc:
                fails += 1
                log.warning("recorder import batch failed (%s…): %s", batch[0], exc)
                continue
            grouped: dict[str, list] = {}
            for s in states:
                grouped.setdefault(s.entity_id, []).append(s)
            for eid, sts in grouped.items():
                b = by_eid.get(eid)
                if b is None:
                    continue
                for j in range(0, len(sts), 5000):
                    await asyncio.to_thread(store.write_raw, b, sts[j:j + 5000])
                counts[b.name] += len(sts)
        cursor = cstop
    total = sum(counts.values())
    log.info("recorder warm-start: imported %d points for %d entities", total, len(eids))
    # systematic failure (HA unreachable / history API erroring) → tell the user
    if repo is not None and batches and fails == batches:
        from ..domain.health import record_issue
        record_issue(repo, "ha_unreachable", "I can't reach Home Assistant",
                     "Couldn't read any history — check Home Assistant is up and reachable.",
                     cta={"label": "Settings", "href": "/settings"})
    return counts

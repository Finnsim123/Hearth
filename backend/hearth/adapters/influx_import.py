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
from datetime import datetime

import pandas as pd

from ..domain.schemas import Binding, EntityState
from .influx_store import InfluxStore

log = logging.getLogger(__name__)


def _frames(store: InfluxStore, flux: str) -> pd.DataFrame:
    df = store.query_api.query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    return df


def import_history(store: InfluxStore, source_bucket: str,
                   bindings: list[Binding], start: datetime, end: datetime) -> dict[str, int]:
    """Returns {binding.name: imported_points}."""
    results: dict[str, int] = {}
    for b in bindings:
        object_id = b.entity_id.split(".", 1)[-1]
        candidates = [
            # merged layout: measurement == full entity id
            f'r._measurement == "{b.entity_id}"',
            # default layout: any measurement, entity_id tag == object id
            f'r["entity_id"] == "{object_id}"',
        ]
        imported = 0
        for cond in candidates:
            flux = f'''
from(bucket: "{source_bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
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
            imported = len(states)
            break
        results[b.name] = imported
        if imported:
            log.info("imported %d points for %s", imported, b.name)
    return results

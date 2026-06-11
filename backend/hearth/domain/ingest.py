"""Ingest service — pillar 1, stage one: HA events -> hearth_raw.

Pure composition of ports: EventSource yields states, TimeSeriesStore writes
them, AppRepo supplies the binding map. Batches writes (5 s flush) and
gap-fills via EventSource.history() on (re)start.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .ports import AppRepo, EventSource, TimeSeriesStore
from .schemas import Binding, EntityState

log = logging.getLogger(__name__)

FLUSH_SECONDS = 5.0


async def gap_fill(events: EventSource, tsdb: TimeSeriesStore,
                   bindings: list[Binding], hours: float = 6.0) -> int:
    """On start/reconnect, backfill missed states from HA's recorder."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    by_entity = {b.entity_id: b for b in bindings}
    try:
        states = await events.history(list(by_entity), start, end)
    except Exception as exc:
        log.warning("gap-fill failed: %s", exc)
        return 0
    per_binding: dict[str, list[EntityState]] = {}
    for st in states:
        if st.entity_id in by_entity:
            per_binding.setdefault(st.entity_id, []).append(st)
    n = 0
    for eid, sts in per_binding.items():
        tsdb.write_raw(by_entity[eid], sts)
        n += len(sts)
    log.info("gap-fill wrote %d states", n)
    return n


async def run_ingest(events: EventSource, tsdb: TimeSeriesStore, repo: AppRepo,
                     signal=None) -> None:
    """Long-running task: subscribe to bound entities, batch, flush. When a
    realtime `signal` is supplied, mark the affected person(s) dirty on every
    change so the realtime lane re-predicts near-instantly."""
    bindings = [b for b in repo.bindings() if b.enabled]
    if not bindings:
        log.info("ingest idle — no bindings configured yet")
        return
    by_entity = {b.entity_id: b for b in bindings}
    persons = [p.id for p in repo.persons() if p.enabled]
    # entity → which person(s) it affects (shared bindings affect everyone)
    affects = {b.entity_id: ([b.person_id] if b.person_id else persons)
               for b in bindings}
    await gap_fill(events, tsdb, bindings)

    buffer: dict[str, list[EntityState]] = {}
    lock = asyncio.Lock()

    async def flusher() -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            async with lock:
                batch, buffer_clear = dict(buffer), buffer.clear()
            for eid, states in batch.items():
                try:
                    tsdb.write_raw(by_entity[eid], states)
                except Exception:
                    log.exception("raw write failed for %s", eid)
            if batch:
                tsdb.write_heartbeat("ingest")

    flush_task = asyncio.create_task(flusher())
    try:
        async for state in events.subscribe(list(by_entity)):
            async with lock:
                buffer.setdefault(state.entity_id, []).append(state)
            if signal is not None:
                signal.mark(affects.get(state.entity_id, ()))
    finally:
        flush_task.cancel()

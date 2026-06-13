"""Recorder warm-start importer: HA history API -> hearth_raw, batched."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hearth.adapters.influx_import import import_recorder_history
from hearth.domain.schemas import Binding, EntityState, Role


class FakeEvents:
    """Records every history() call and returns one state per requested entity."""
    def __init__(self):
        self.calls = []

    async def history(self, entity_ids, start, end):
        self.calls.append((tuple(entity_ids), start, end))
        mid = start + (end - start) / 2
        return [EntityState(entity_id=e, state="on", ts=mid) for e in entity_ids]


class FakeStore:
    def __init__(self):
        self.writes = []

    def write_raw(self, binding, states):
        self.writes.append((binding.name, len(states)))


@pytest.mark.asyncio
async def test_import_recorder_history_batches_and_counts():
    bindings = [
        Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE, name="sofa"),
        Binding(entity_id="light.kitchen", role=Role.LIGHT, name="kitchen"),
        Binding(entity_id="person.alice", role=Role.PERSON, name="alice_loc"),
    ]
    events, store = FakeEvents(), FakeStore()
    end = datetime(2026, 6, 10, tzinfo=timezone.utc)
    start = end - timedelta(days=4)

    counts = await import_recorder_history(
        events, store, bindings, start, end, entity_batch=2, day_chunk=2)

    # 4-day span / 2-day chunks = 2 time windows; 3 entities / batch 2 = 2 batches
    assert len(events.calls) == 2 * 2
    # one state per entity per time chunk → 2 each
    assert counts == {"sofa": 2, "kitchen": 2, "alice_loc": 2}
    # every state was written to the store under the right binding name
    assert sum(n for _, n in store.writes) == 6


@pytest.mark.asyncio
async def test_import_recorder_history_survives_a_failing_batch():
    class FlakyEvents(FakeEvents):
        async def history(self, entity_ids, start, end):
            if "light.kitchen" in entity_ids:
                raise RuntimeError("HA timeout")
            return await super().history(entity_ids, start, end)

    bindings = [
        Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE, name="sofa"),
        Binding(entity_id="light.kitchen", role=Role.LIGHT, name="kitchen"),
    ]
    events, store = FlakyEvents(), FakeStore()
    end = datetime(2026, 6, 10, tzinfo=timezone.utc)
    counts = await import_recorder_history(
        events, store, bindings, end - timedelta(days=2), end,
        entity_batch=1, day_chunk=2)
    # kitchen's batch raised and was skipped; sofa still imported
    assert counts["sofa"] == 1 and counts["kitchen"] == 0

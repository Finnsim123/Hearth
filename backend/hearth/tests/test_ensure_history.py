"""ensure_history — feature backfill after a feature-set change.

The bug it fixes: approving a new device (integrate) changes the fset hash,
orphaning ALL history windows under the old hash; training then skips for
hours and the first model back is fit on a sliver of data.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from hearth.domain.features.pipeline import ensure_history
from hearth.domain.schemas import Binding, Role

T0 = datetime(2026, 7, 26, tzinfo=timezone.utc)
T1 = datetime(2026, 7, 29, tzinfo=timezone.utc)


class FakeTsdb:
    def __init__(self):
        self.written: list[tuple[str, str, int]] = []
    def read_raw(self, bindings, start, end):
        return pd.DataFrame()
    def write_features(self, person_id, fset, feats):
        self.written.append((person_id, fset, len(feats)))


class FakeRepo:
    def __init__(self, promoted=True, with_binding=True):
        self._promoted = promoted
        self._binding = with_binding
    def bindings(self):
        if not self._binding:
            return []
        return [Binding(entity_id="binary_sensor.hall", role=Role.PRESENCE,
                        name="hall_motion")]
    def models(self, person_id=None):
        from types import SimpleNamespace
        return [SimpleNamespace(promoted=self._promoted)]
    def get_setting(self, key, default=None):
        return default


def test_rebuilds_chunked_for_persons_that_trained_before():
    tsdb = FakeTsdb()
    built = ensure_history(tsdb, FakeRepo(), "alex", T0, T1,
                           have=7, need=100, chunk_days=2)
    assert built > 0
    assert len(tsdb.written) == 2            # 3 days in 2-day chunks -> 2 writes
    # windows land on the 30-min grid across the whole span
    assert sum(n for _, _, n in tsdb.written) == built
    assert built >= 100                      # 3 days x 48/day


def test_cold_start_never_fabricates_windows():
    tsdb = FakeTsdb()
    assert ensure_history(tsdb, FakeRepo(promoted=False), "alex", T0, T1,
                          have=0, need=100) == 0
    assert tsdb.written == []


def test_noop_when_enough_windows_exist():
    tsdb = FakeTsdb()
    assert ensure_history(tsdb, FakeRepo(), "alex", T0, T1,
                          have=250, need=100) == 0
    assert tsdb.written == []

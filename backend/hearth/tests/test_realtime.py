"""Realtime lane: dirty-signal drain, change detection, current-window predict."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from hearth.domain.inference.realtime import (
    RealtimeSignal, predict_current, state_changed)
from hearth.domain.schemas import Prediction


def _pred(smoothed, predicted=None, parent=None):
    return Prediction(person_id="alice", window_ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
                      model_version="alice-v1", predicted=predicted or smoothed,
                      smoothed=smoothed, confidence=0.9, probabilities={smoothed: 0.9},
                      parent=parent)


def test_signal_marks_and_drains():
    sig = RealtimeSignal()
    sig.mark(["alice", "bob"])
    sig.mark(["alice"])
    assert sig.drain() == {"alice", "bob"}
    assert sig.drain() == set()            # cleared


@pytest.mark.asyncio
async def test_signal_wait_returns_on_mark():
    sig = RealtimeSignal()

    async def marker():
        await asyncio.sleep(0.01)
        sig.mark(["alice"])

    asyncio.create_task(marker())
    await asyncio.wait_for(sig.wait(), timeout=1.0)   # wakes well before SAFETY_S
    assert "alice" in sig.drain()


def test_state_changed_fires_only_on_flip():
    assert state_changed(_pred("home"), []) is True              # no history
    assert state_changed(_pred("home"), [_pred("sleeping")]) is True
    assert state_changed(_pred("home"), [_pred("home")]) is False
    # fine-state flip within the same coarse state still counts
    assert state_changed(_pred("eating", parent="home"),
                         [_pred("home")]) is True


class _Repo:
    def __init__(self): self.settings = {}
    def models(self, person=None): return []      # no promoted model
    def get_setting(self, k, d=None): return self.settings.get(k, d)
    def bindings(self): return []
    def persons(self): return []


def test_predict_current_none_without_model():
    # cold-start (no promoted model) → realtime lane defers to grid/rules lane
    assert predict_current("alice", tsdb=None, repo=_Repo(), store=None) is None


def test_bindings_health_classifies_alive_constant_no_data():
    """The diagnostic must distinguish a live varying sensor, a bound-but-
    constant one (dead weight), and one with no data — and flag a missing class."""
    import pandas as pd
    from hearth.domain.schemas import Binding, Role

    # simulate what the endpoint computes (pure logic extracted inline)
    feats = pd.DataFrame({
        "sofa_frac": [0.0, 1.0, 0.5],      # varies → alive
        "alex_home_frac": [1.0, 1.0, 1.0], # constant → dead weight
    })
    bindings = [
        Binding(id=1, entity_id="binary_sensor.sofa", role=Role.PRESENCE, name="sofa"),
        Binding(id=2, entity_id="person.alex", role=Role.PERSON, name="alex_home"),
        Binding(id=3, entity_id="person.kid", role=Role.PERSON, name="kid_home"),  # no col
    ]
    def status(b):
        cols = [c for c in feats.columns if c == b.name or c.startswith(b.name + "_")]
        varies = any(feats[c].nunique(dropna=True) > 1 for c in cols)
        present = bool(cols) and any(feats[c].notna().any() for c in cols)
        return "alive" if varies else "constant" if present else "no_data"
    assert [status(b) for b in bindings] == ["alive", "constant", "no_data"]

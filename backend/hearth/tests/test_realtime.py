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

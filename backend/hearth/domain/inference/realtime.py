"""Realtime inference lane — near-instant predictions for automations.

The grid lane (predict_latest) runs every 5 min on 30-min windows aligned to a
5-min stride: great for the ribbon/history, useless for "dim the lights AS the
movie starts". This lane is event-driven:

    bound sensor changes (ingest WebSocket)  →  RealtimeSignal.mark(person)
    →  realtime_loop wakes (debounced)  →  predict a window ending NOW
    →  if the smoothed state CHANGED, fire `hearth_activity_changed` on HA's
       event bus so automations trigger instantly (no polling lag).

Cheap by design: no SHAP/evidence on this path (that's the grid lane's job for
the dashboard) — just model → hierarchy → transition filter → hysteresis, so it
can run on every sensor change without loading the CT.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..features.pipeline import (WINDOW, bindings_for_person, compute_features,
                                  prepare)
from ..features.registry import feature_set_version
from ..schemas import Prediction
from .smoothing import smooth, transition_filter

log = logging.getLogger(__name__)

DEBOUNCE_S = 3.0      # coalesce a burst of sensor events into one prediction
SAFETY_S = 60.0       # also re-predict at least this often, even with no events


class RealtimeSignal:
    """Shared between the ingest task and the realtime loop (same event loop)."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._dirty: set[str] = set()

    def mark(self, person_ids) -> None:
        self._dirty.update(person_ids)
        self._event.set()

    async def wait(self) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=SAFETY_S)
        except asyncio.TimeoutError:
            pass  # safety tick — fall through and predict everyone

    def drain(self) -> set[str]:
        """Snapshot + clear; empty set means 'safety tick → predict all'."""
        out, self._dirty = self._dirty, set()
        self._event.clear()
        return out


def current_window_features(tsdb, repo, person_id: str):
    """ONE feature row for the window ending NOW (in-memory, never persisted —
    off-grid rows must not pollute the training feature store)."""
    bindings = bindings_for_person(repo.bindings(), person_id)
    if not bindings:
        return pd.DataFrame()
    composites = repo.get_setting("composites", []) or []
    lag_features = repo.get_setting("lag_features", []) or []
    tz = repo.get_setting("timezone", "UTC") or "UTC"
    end = datetime.now(timezone.utc)
    start = end - WINDOW
    raw = tsdb.read_raw(bindings, start - timedelta(minutes=120), end)
    prepared = prepare(raw, bindings) if not raw.empty else raw
    return compute_features(prepared, bindings, [start], tz, composites, lag_features)


def predict_current(person_id: str, tsdb, repo, store) -> Prediction | None:
    """Predict the live window. Returns None when there's no promoted model
    (the grid/rules lane covers cold-start) or no data."""
    promoted = [m for m in repo.models(person_id) if m.promoted]
    record = next((m for m in promoted if m.node == "root"), None)
    if record is None:
        return None
    feats = current_window_features(tsdb, repo, person_id)
    if feats.empty:
        return None
    ts = feats.index[-1]
    est = store.load(record)
    row = est.predict_proba(feats).iloc[-1]

    # recent history for transition prior + hysteresis (newest first)
    now = datetime.now(timezone.utc)
    history = _recent(tsdb, person_id, now)
    trans = repo.get_setting(f"transitions.{person_id}") or None
    if trans and history:
        prev = history[0]
        row = transition_filter(row, prev.parent or prev.predicted, trans)
    predicted = str(row.idxmax())
    confidence = float(row.max())
    parent = None
    coarse_confidence = None
    for child in (m for m in promoted if m.node == predicted):
        try:
            fine_row = store.load(child).predict_proba(feats).iloc[-1]
        except Exception:
            break
        coarse_confidence = confidence
        fine = str(fine_row.idxmax())
        if fine != predicted:
            parent, predicted = predicted, fine
        confidence, row = float(fine_row.max()), fine_row
        break
    smoothed = smooth(history, predicted, confidence)
    return Prediction(person_id=person_id, window_ts=ts.to_pydatetime(),
                      model_version=record.version, predicted=predicted,
                      smoothed=smoothed, confidence=confidence,
                      probabilities={c: float(v) for c, v in row.items()},
                      parent=parent, coarse_confidence=coarse_confidence)


def _recent(tsdb, person_id: str, now: datetime) -> list[Prediction]:
    out = []
    for r in tsdb.read_predictions(person_id, now - timedelta(hours=3), now):
        out.append(Prediction(person_id=person_id,
                              window_ts=datetime.fromisoformat(r["time"]),
                              model_version=r["model_version"], predicted=r["predicted"],
                              smoothed=r["smoothed"], confidence=r["confidence"],
                              probabilities=r.get("probs", {}), parent=r.get("parent")))
    return out


def state_changed(pred: Prediction, history: list[Prediction]) -> bool:
    """True when the published (smoothed) coarse-or-fine state differs from the
    most recent prediction — the trigger for an HA event + a fresh write."""
    if not history:
        return True
    last = history[0].smoothed or history[0].predicted
    return (pred.smoothed or pred.predicted) != last


async def realtime_loop(tsdb, repo, store, signal: RealtimeSignal,
                        notifier=None) -> None:
    """Long-running: wake on sensor change, predict the live window, push to HA
    on state change. Runs alongside the 5-min grid lane."""
    log.info("realtime inference lane started")
    while True:
        await signal.wait()
        await asyncio.sleep(DEBOUNCE_S)
        dirty = signal.drain()
        persons = repo.persons()
        targets = [p for p in persons
                   if p.enabled and (not dirty or p.id in dirty)]
        for person in targets:
            try:
                pred = await asyncio.to_thread(predict_current, person.id,
                                               tsdb, repo, store)
            except Exception:
                log.exception("realtime predict failed for %s", person.id)
                continue
            if pred is None:
                continue
            now = datetime.now(timezone.utc)
            history = _recent(tsdb, person.id, now)
            if not state_changed(pred, history):
                continue
            tsdb.write_prediction(pred)
            log.info("[realtime] %s → %s (%.0f%%)", person.id,
                     pred.smoothed, pred.confidence * 100)
            if notifier is not None and hasattr(notifier, "fire_event"):
                try:
                    await notifier.fire_event("hearth_activity_changed", {
                        "person": person.id, "person_name": person.name,
                        "state": pred.parent or pred.smoothed,   # coarse: stable
                        "activity": pred.smoothed,                # fine if any
                        "confidence": round(pred.confidence, 3),
                    })
                except Exception:
                    log.exception("fire_event failed")

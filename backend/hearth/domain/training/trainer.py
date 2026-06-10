"""Training service — one run per person, scheduled weekly + UI 'Train now'.

Steps: load feature matrix (single feature_set) -> merged labels -> temporal
split -> fit Estimator -> evaluate (training/evaluate.py) -> registry record
-> promotion gate (confirmed-accuracy CI overlap, not point estimates) ->
artifact save. Streams progress lines for the UI's live log over WebSocket.
"""
from __future__ import annotations

from ..ports import AppRepo, Estimator, ModelStore, TimeSeriesStore
from ..schemas import ModelRecord


def train_person(
    person_id: str,
    tsdb: TimeSeriesStore,
    repo: AppRepo,
    store: ModelStore,
    weeks: int = 8,
    force: bool = False,
) -> ModelRecord:
    """Skips (unless force) when too few NEW confirmed labels since last run.
    Never trains across a feature_set boundary."""
    raise NotImplementedError


def promotion_gate(new: ModelRecord, current: ModelRecord | None) -> bool:
    """Promote iff confirmed-accuracy CI of new is not clearly below current's
    (Wilson intervals; tiny-n honesty — see RESEARCH.md P6)."""
    raise NotImplementedError


def rollback(person_id: str, repo: AppRepo) -> ModelRecord:
    """Repoint to the previous promoted model."""
    raise NotImplementedError

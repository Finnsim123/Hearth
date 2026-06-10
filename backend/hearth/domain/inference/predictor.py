"""Inference service — newest feature window -> Prediction (per person).

Loads the promoted model, predicts, attaches top-k SHAP explanation, applies
temporal smoothing, writes to hearth_ml, publishes to HA, then hands off to
the asking policy. Manual override (HA select entity) short-circuits with
confidence 1.0 and writes a confirmed label.
"""
from __future__ import annotations

from ..ports import AppRepo, EntityPublisher, ModelStore, TimeSeriesStore
from ..schemas import Prediction


def predict_latest(
    tsdb: TimeSeriesStore,
    repo: AppRepo,
    store: ModelStore,
    publisher: EntityPublisher,
) -> list[Prediction]:
    """Scheduler entrypoint — one prediction per enabled person per new window."""
    raise NotImplementedError

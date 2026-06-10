"""Window builder — pillar 1's engine.

raw (1-min, role-aware ffill) -> per-binding recipes -> composites -> impute
-> persist to hearth_features. Runs on a schedule; identical code path feeds
training matrices and the single inference row (no train/serve skew by
construction, ADR-7).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..ports import AppRepo, TimeSeriesStore


def build_windows(
    tsdb: TimeSeriesStore,
    repo: AppRepo,
    person_id: str,
    start: datetime,
    end: datetime,
    stride_min: int = 30,
) -> pd.DataFrame:
    """Materialize all 30-min windows in [start, end) for one person.

    stride 30 for training (non-overlapping), stride 5 for live inference.
    Returns the feature matrix AND writes it to the feature store.
    """
    raise NotImplementedError


def build_latest_windows(tsdb: TimeSeriesStore, repo: AppRepo) -> None:
    """Scheduler entrypoint: build any windows newer than the last persisted
    one for every enabled person, then write a heartbeat."""
    raise NotImplementedError


def impute(features: pd.DataFrame) -> pd.DataFrame:
    """Semantic imputation driven by role metadata:
    absence_value 0 ('no event') vs -1 ('sensor absent') — never statistical."""
    raise NotImplementedError

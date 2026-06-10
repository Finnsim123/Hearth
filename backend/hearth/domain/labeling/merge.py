"""Provenance-aware label merge.

Training labels = bootstrap (rules) overlaid by discovered (named clusters)
overlaid by confirmed (humans). Identity key: (person, window_ts, window).
Also resolves stage-1 label + stage-2 sub-activity into the leaf slug when a
sub-activity exists (the prototype lost stage-2 answers entirely — lesson #x:
read both fields, prefer the leaf).
"""
from __future__ import annotations

import pandas as pd

from ..schemas import LabelEvent


def merge_labels(
    bootstrap: pd.Series,
    events: list[LabelEvent],
) -> tuple[pd.Series, pd.Series]:
    """Returns (labels, provenance) series aligned to the feature index.
    Confirmed > discovered > bootstrap; within a provenance, latest wins."""
    raise NotImplementedError

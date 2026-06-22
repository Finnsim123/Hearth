"""Provenance-aware label merge — trust order decides, latest wins per tier.

bootstrap (rules) < llm (weak annotator) < discovered (named cluster)
< confirmed (human). Identity key: window start floored to the 30-min grid.
Also resolves stage-2 sub-activity into the leaf label when present.
"""
from __future__ import annotations

import pandas as pd

from ..schemas import LabelEvent, Provenance

_TRUST = {Provenance.BOOTSTRAP: 0, Provenance.LLM: 1,
          Provenance.DISCOVERED: 2, Provenance.CONFIRMED: 3}


def merge_labels(
    bootstrap: pd.Series,
    events: list[LabelEvent],
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (labels, provenance, gold) aligned to bootstrap.index (UTC windows).
    `gold` flags confirmed windows answered to a random ε-explore ask — the
    unbiased subset the honest headline metric is measured on (audit F1)."""
    labels = bootstrap.copy()
    provenance = pd.Series(Provenance.BOOTSTRAP.value, index=bootstrap.index, dtype=object)
    gold = pd.Series(False, index=bootstrap.index, dtype=bool)
    floored = bootstrap.index.floor("30min")
    by_window: dict[pd.Timestamp, LabelEvent] = {}
    for ev in events:
        key = pd.Timestamp(ev.window_ts).tz_convert("UTC").floor("30min")
        cur = by_window.get(key)
        if cur is None or _TRUST[ev.provenance] >= _TRUST[cur.provenance]:
            by_window[key] = ev
    for key, ev in by_window.items():
        mask = floored == key
        if mask.any():
            labels[mask] = ev.activity or ev.label  # leaf slug wins when present
            provenance[mask] = ev.provenance.value
            gold[mask] = bool(ev.gold)
    return labels, provenance, gold

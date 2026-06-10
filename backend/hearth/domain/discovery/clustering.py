"""Unsupervised pattern discovery — the cluster-then-name loop (ARCH §6).

Nightly over ~30 days of feature windows (later: HEPA embeddings behind the
Embedder port): scale -> reduce (UMAP) -> HDBSCAN -> signature extraction ->
ClusterCard rows for the UI. Naming a card emits provenance=DISCOVERED labels
for its windows and drafts a Rule (labeling/rules.draft_rule_from_signature).

Clusters are PROPOSALS, never labels (RESEARCH.md P7): the UI supports name /
merge / dismiss / split-by-time.
"""
from __future__ import annotations

import pandas as pd

from ..ports import AppRepo, TimeSeriesStore
from ..schemas import ClusterCard


def run_discovery(tsdb: TimeSeriesStore, repo: AppRepo, days: int = 30) -> list[ClusterCard]:
    """Scheduler entrypoint. Skips windows already confirmed-labeled (they
    don't need discovery); dedupes against previously named clusters by
    signature similarity."""
    raise NotImplementedError


def signature(cluster_rows: pd.DataFrame, global_stats: pd.DataFrame, top_k: int = 6) -> list:
    """Top distinguishing features as (feature, z-score) — rendered on cards
    as e.g. 'sofa ↑ · media playing ↑ · evening'."""
    raise NotImplementedError

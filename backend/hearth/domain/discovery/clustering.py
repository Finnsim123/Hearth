"""Unsupervised pattern discovery — the cluster-then-name loop (ARCH §6).

Weekly (or on demand) over ~30 days of feature windows, per person:
standardize → HDBSCAN (sklearn's, no extra deps) → signature extraction →
ClusterCard rows for the Patterns page. Naming a card emits
provenance=DISCOVERED labels for ALL its windows — one click labels weeks
of history the bootstrap rules couldn't explain.

Clusters are PROPOSALS, never labels (RESEARCH.md P7): the UI supports
name / dismiss; confirmed windows are excluded from clustering (they don't
need discovery), and re-runs dedupe against already-handled cards by
signature overlap.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..schemas import ClusterCard, Provenance

log = logging.getLogger(__name__)

MIN_WINDOWS = 120          # ≈ 60 h of recording — the 72 h acceptance bar (ROADMAP P4)
MAX_MEMBER_WINDOWS = 1000  # stored per card (labeling cap)
from ..features.pipeline import TEMPORAL_COLS
TOP_K = 6                  # signature size
DEDUPE_OVERLAP = 3         # shared top-4 features with a handled card → skip


def signature(cluster_rows: pd.DataFrame, global_mean: pd.Series,
              global_std: pd.Series, top_k: int = TOP_K) -> list[tuple[str, float]]:
    """Top distinguishing features as (feature, z-score) — what makes this
    cluster THIS cluster, rendered as 'sofa ↑ · media playing ↑ · …'."""
    z = (cluster_rows.mean() - global_mean) / global_std.replace(0, np.nan)
    z = z.dropna()
    top = z.abs().sort_values(ascending=False).head(top_k)
    return [(feat, round(float(z[feat]), 2)) for feat in top.index]


def _hour_histogram(index: pd.DatetimeIndex, tz: str) -> list[int]:
    hist = [0] * 24
    try:
        hours = index.tz_convert(ZoneInfo(tz)).hour
    except Exception:
        hours = index.hour
    for h in hours:
        hist[int(h)] += 1
    return hist


def _is_duplicate(sig: list[tuple[str, float]], handled: list[ClusterCard]) -> bool:
    mine = {f for f, _ in sig[:4]}
    for card in handled:
        theirs = {f for f, _ in card.signature[:4]}
        if len(mine & theirs) >= DEDUPE_OVERLAP:
            return True
    return False


def discover_person(person_id: str, tsdb, repo, days: int = 30) -> list[ClusterCard]:
    """Cluster one person's recent unexplained windows into pattern candidates."""
    from sklearn.cluster import HDBSCAN

    from ..features.registry import active_feature_set_version

    fset = active_feature_set_version(repo)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    feats = tsdb.read_features(person_id, fset, start, end)
    if len(feats) < MIN_WINDOWS:
        log.info("[discovery:%s] %d windows < %d — skip", person_id, len(feats), MIN_WINDOWS)
        return []

    # confirmed windows are already explained — discovery is for the rest
    confirmed = {ev.window_ts for ev in tsdb.read_labels(person_id, start, end)
                 if ev.provenance == Provenance.CONFIRMED}
    if confirmed:
        feats = feats[~feats.index.isin(confirmed)]

    from ..features.person_scope import drop_foreign_personal
    feats, _ = drop_foreign_personal(feats, repo.bindings(), repo.persons(), person_id)

    X = feats.drop(columns=[c for c in TEMPORAL_COLS if c in feats.columns])
    X = X.loc[:, X.std() > 0]                      # constant features carry nothing
    if X.empty or len(X) < MIN_WINDOWS:
        return []
    g_mean, g_std = X.mean(), X.std()
    Xs = ((X - g_mean) / g_std).fillna(0.0)

    min_size = max(8, len(Xs) // 40)               # small installs still find patterns
    labels = HDBSCAN(min_cluster_size=min_size).fit_predict(Xs.to_numpy())

    tz = repo.get_setting("timezone", "UTC") or "UTC"
    handled = [c for c in repo.clusters() if c.status != "new"]
    cards: list[ClusterCard] = []
    for cl in sorted(set(labels) - {-1}):
        rows = X[labels == cl]
        sig = signature(rows, g_mean, g_std)
        if _is_duplicate(sig, handled):
            continue
        members = list(rows.index[:MAX_MEMBER_WINDOWS])
        cards.append(ClusterCard(
            person_id=person_id, algo="hdbscan", n_windows=int(len(rows)),
            signature=sig, hour_histogram=_hour_histogram(rows.index, tz),
            example_windows=[ts.to_pydatetime() for ts in members]))
    log.info("[discovery:%s] %d windows → %d pattern candidates",
             person_id, len(X), len(cards))
    return cards


def merge_clusters(source: ClusterCard, target: ClusterCard) -> tuple[ClusterCard, ClusterCard]:
    """Fold `source` into `target`: windows united (deduped), histogram summed.
    Source survives as status='merged' so re-runs dedupe against it."""
    seen = set(target.example_windows)
    target.example_windows = target.example_windows + [
        t for t in source.example_windows if t not in seen]
    target.n_windows = len(target.example_windows)
    target.hour_histogram = [a + b for a, b in
                             zip(target.hour_histogram, source.hour_histogram)]
    source.status = "merged"
    return source, target


def run_discovery(tsdb, repo, days: int = 30) -> list[ClusterCard]:
    """Scheduler/API entrypoint: replace each person's un-named candidates
    with a fresh run (named/dismissed cards are kept and deduped against)."""
    saved: list[ClusterCard] = []
    for person in repo.persons():
        if not person.enabled:
            continue
        cards = discover_person(person.id, tsdb, repo, days=days)
        repo.clear_clusters(person.id, status="new")
        for c in cards:
            saved.append(repo.save_cluster(c))
    return saved

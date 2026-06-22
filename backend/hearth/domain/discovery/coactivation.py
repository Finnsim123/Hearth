"""Sensor co-activation clustering — group sensors that FIRE TOGETHER, not by
their nominal room label. Two motion sensors on the same morning route, or a
hob + extractor that always run as a pair, light up at the same times; the
correlation of their per-bin change intensity reveals those functional zones
the room name can't.

Used by the Sensor coverage map's 'By behaviour' lens (frontend). The room
labels stay the default; this is an alternative grouping that comes from the
data, not the config. Layout coords (0..1) come from an MDS embedding of the
SAME co-activation distance, so two clusters drawn near each other genuinely
behave alike — position carries meaning instead of being decorative.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

ACT_BIN = "5min"        # co-activation resolution — coarse enough to co-fire
MIN_SENSORS = 4         # below this, clustering is noise; caller falls back to rooms
# average-linkage cut on 1-corr distance: merge while correlation stays above
# (1 - THRESHOLD). 0.7 → groups sensors whose activity correlates above ~0.3.
THRESHOLD = 0.7


def _activity(raw: pd.DataFrame, bin_size: str = ACT_BIN) -> pd.DataFrame:
    """Per-sensor change intensity per time bin. A sensor 'fires' in a bin when
    its value moves; counting moves per bin gives an activity series whose
    correlation across sensors is exactly co-activation."""
    if raw.empty:
        return raw
    moves = raw.ffill().diff().abs() > 1e-9          # value changed since last sample
    return moves.resample(bin_size).sum().astype(float)


def cluster_sensors(raw: pd.DataFrame, *, min_sensors: int = MIN_SENSORS,
                    threshold: float = THRESHOLD) -> dict:
    """Cluster sensors by co-activation over a wide 1-min raw frame.

    Returns {"clusters": [{"id", "x", "y", "n"}], "assign": {name: cluster_id}}.
    `x`/`y` are 0..1 layout seeds from an MDS embedding of the distance matrix.
    Empty result (clusters=[]) when there's too little signal — the caller then
    keeps the room grouping."""
    act = _activity(raw)
    if act.empty:
        return {"clusters": [], "assign": {}}
    act = act.loc[:, act.sum() > 0]                  # drop sensors that never moved
    names = list(act.columns)
    if len(names) < min_sensors:
        return {"clusters": [], "assign": {}}

    corr = act.corr().fillna(0.0).to_numpy()
    dist = 1.0 - corr
    dist = np.clip((dist + dist.T) / 2.0, 0.0, 2.0)  # symmetric, non-negative
    np.fill_diagonal(dist, 0.0)

    from sklearn.cluster import AgglomerativeClustering
    from sklearn.manifold import MDS

    labels = AgglomerativeClustering(
        n_clusters=None, metric="precomputed", linkage="average",
        distance_threshold=threshold).fit_predict(dist)

    # stable 2D layout from the SAME distances → near clusters behave alike.
    # Fixed seed keeps the map from reshuffling between 30s refreshes.
    try:
        coords = MDS(n_components=2, dissimilarity="precomputed", random_state=42,
                     n_init=4, normalized_stress="auto").fit_transform(dist)
    except Exception as exc:                          # MDS can fail to converge
        log.warning("co-activation MDS failed: %s — falling back to grid", exc)
        coords = np.column_stack([np.arange(len(names)), np.zeros(len(names))]).astype(float)

    assign = {name: int(lbl) for name, lbl in zip(names, labels)}
    clusters = []
    for cl in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == cl]
        clusters.append({"id": int(cl), "x": float(coords[idx, 0].mean()),
                         "y": float(coords[idx, 1].mean()), "n": len(idx)})

    # min-max each axis into 0..1 so the frontend can scale to any canvas width
    def _norm(vals: list[float]) -> list[float]:
        lo, hi = min(vals), max(vals)
        rng = hi - lo
        return [0.5 if rng < 1e-9 else (v - lo) / rng for v in vals]

    nx = _norm([c["x"] for c in clusters])
    ny = _norm([c["y"] for c in clusters])
    for c, x, y in zip(clusters, nx, ny):
        c["x"], c["y"] = round(x, 4), round(y, 4)

    log.info("co-activation: %d sensors → %d clusters", len(names), len(clusters))
    return {"clusters": clusters, "assign": assign}

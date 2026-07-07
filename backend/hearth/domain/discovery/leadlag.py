"""Lead/lag discovery — the home's temporal WIRING.

Co-activation (coactivation.py) finds sensors that fire *together*; this finds
which sensor reliably *precedes* another, and by how long: bathroom motion →
bedroom light-off (~2 min), kitchen motion → hob (~5 min), front door → hallway
light. The lagged cross-correlation of each pair's per-minute activity recovers a
directed graph the config never states — candidate markers/transitions, and a
legible "how your home flows" view.

The paper's "lagged correlations (e.g. minutes spoken vs SMS)" idea, indoors.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BIN = "1min"            # lead/lag resolution — minutes matter here
MAX_LAG_MIN = 15
MIN_STRENGTH = 0.2      # peak cross-correlation to keep an edge
TOP_EDGES = 24
MIN_SENSORS = 3
MAX_SENSORS = 16        # cap to bound the O(M² · lag) pass


def _activity(raw: pd.DataFrame, bin_size: str = BIN) -> pd.DataFrame:
    """Per-sensor change intensity per minute, lightly smoothed (3-min rolling)
    so a burst of one sensor can lead a burst of another."""
    if raw.empty:
        return raw
    moves = raw.ffill().diff().abs() > 1e-9
    a = moves.resample(bin_size).sum().astype(float)
    return a.rolling(3, min_periods=1).sum()


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 8:
        return 0.0
    sa, sb = a.std(), b.std()
    if sa < 1e-9 or sb < 1e-9:
        return 0.0
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def _best_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[float, int]:
    """Best positive cross-correlation across lags. k>0 → a leads b by k minutes;
    k<0 → b leads a; k=0 → no lead/lag signal above the peak."""
    best_r, best_k = 0.0, 0
    for k in range(1, max_lag + 1):
        r1 = _corr(a[:-k], b[k:])       # a leads b by k
        if r1 > best_r:
            best_r, best_k = r1, k
        r2 = _corr(b[:-k], a[k:])       # b leads a by k
        if r2 > best_r:
            best_r, best_k = r2, -k
    return best_r, best_k


def lead_lag_edges(raw: pd.DataFrame, *, max_lag_min: int = MAX_LAG_MIN,
                   min_strength: float = MIN_STRENGTH, top: int = TOP_EDGES,
                   max_sensors: int = MAX_SENSORS) -> list[dict]:
    """Directed lead→lag edges [{from, to, lag_min, strength}] over a wide raw
    frame, strongest first. Empty when there's too little signal."""
    act = _activity(raw)
    if act.empty:
        return []
    act = act.loc[:, act.sum() > 0]                 # drop sensors that never moved
    if act.shape[1] > max_sensors:                  # keep the most active
        act = act[act.sum().sort_values(ascending=False).index[:max_sensors]]
    names = list(act.columns)
    if len(names) < MIN_SENSORS:
        return []
    arr = {n: act[n].to_numpy(dtype=float) for n in names}
    edges: list[dict] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a_name, b_name = names[i], names[j]
            r, k = _best_lag(arr[a_name], arr[b_name], max_lag_min)
            if r < min_strength or k == 0:
                continue
            frm, to, lag = (a_name, b_name, k) if k > 0 else (b_name, a_name, -k)
            edges.append({"from": frm, "to": to, "lag_min": int(lag),
                          "strength": round(r, 3)})
    edges.sort(key=lambda e: -e["strength"])
    log.info("lead/lag: %d sensors → %d edges", len(names), len(edges))
    return edges[:top]

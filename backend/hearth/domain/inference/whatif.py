"""What-If probe (UX8) — interactive counterfactual on the live model.

Pick a window, see the prediction + calibrated confidence + top SHAP signals, then
perturb a feature and watch the prediction move. The Google PAIR What-If Tool / LIT
idiom, made conversational-or-visual. Root model only (the fine hierarchy is omitted
in the probe — it's a transparency tool, not the production path).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

TOP_K = 6


def probe_window(person_id: str, tsdb, repo, store,
                 window_ts: str | None = None,
                 overrides: dict | None = None, top_k: int = TOP_K) -> dict:
    """Score one (optionally edited) feature row through the promoted root model.
    Returns prediction, confidence, top probabilities, the editable top features
    with their current values, and the SHAP explanation for this window."""
    from ..features.registry import active_feature_set_version

    record = next((m for m in repo.models(person_id)
                   if m.promoted and m.node == "root"), None)
    if record is None:
        return {"error": "no_model"}
    est = store.load(record)
    fset = active_feature_set_version(repo)
    now = datetime.now(timezone.utc)

    if window_ts:
        ts = pd.Timestamp(window_ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        feats = tsdb.read_features(person_id, fset,
                                   ts - timedelta(minutes=40), ts + timedelta(minutes=40))
    else:
        feats = tsdb.read_features(person_id, fset, now - timedelta(hours=3), now)
        ts = None
    if feats.empty:
        return {"error": "no_features"}

    row_ts = (min(feats.index, key=lambda t: abs((t - ts).total_seconds()))
              if ts is not None else feats.index[-1])
    row = feats.loc[[row_ts]].copy()

    for k, v in (overrides or {}).items():
        if k in row.columns:
            try:
                row.iloc[0, row.columns.get_loc(k)] = float(v)
            except (TypeError, ValueError):
                pass

    probs = est.predict_proba(row).iloc[0]
    explains = est.explain(row)
    top_probs = probs.sort_values(ascending=False).head(5)

    imp = est.importances() or {}
    cols = [c for c, _ in sorted(imp.items(), key=lambda kv: -kv[1])
            if c in row.columns][:top_k]
    features = {c: round(float(row.iloc[0][c]), 4) for c in cols}

    explanation: list[list] = []
    if not explains.empty and row_ts in explains.index:
        top = explains.loc[row_ts].abs().nlargest(top_k)
        explanation = [[f, round(float(explains.loc[row_ts, f]), 4)] for f in top.index]

    return {
        "window_ts": row_ts.isoformat(),
        "predicted": str(probs.idxmax()),
        "confidence": round(float(probs.max()), 4),
        "probabilities": {c: round(float(v), 4) for c, v in top_probs.items()},
        "features": features,
        "explanation": explanation,
        "edited": sorted(overrides or {}),
    }

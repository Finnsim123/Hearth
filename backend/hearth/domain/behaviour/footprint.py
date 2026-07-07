"""Home footprint — the mobility features (pipeline.py: mob_*) surfaced as a
plain-language behaviour panel, so they're legible to a person, not just fuel for
the model. Descriptive only (rooms touched, how spread, how much moving about) —
deliberately NOT read as a health signal.

Reads the feature store directly (independent of predictions), aggregates the
display window for the headline, and compares the last 7 days with the prior 7
for a gentle trend.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

_MOB = ("mob_rooms_active", "mob_room_entropy", "mob_room_switches", "mob_top_room_frac")


def _roaming_label(entropy: float | None) -> str:
    if entropy is None:
        return ""
    if entropy < 0.25:
        return "mostly settled in one room"
    if entropy < 0.6:
        return "moves between a few rooms"
    return "roams widely across the home"


def _pacing_label(switches: float | None) -> str:
    if switches is None:
        return ""
    if switches < 1.0:
        return "calm"
    if switches < 4.0:
        return "some moving about"
    return "lots of moving about"


def _agg(feats) -> dict | None:
    """Mean mobility over the ACTIVE windows (those with any room activity)."""
    have = [c for c in _MOB if c in feats.columns]
    if not have or feats.empty:
        return None
    active = feats[feats["mob_rooms_active"] > 0] if "mob_rooms_active" in feats else feats
    if active.empty:
        return None

    def m(col):
        return float(active[col].mean()) if col in active else None
    return {"rooms": m("mob_rooms_active"), "roaming": m("mob_room_entropy"),
            "pacing": m("mob_room_switches"), "settled": m("mob_top_room_frac"),
            "windows": int(len(active))}


def footprint(tsdb, repo, person_id: str, days: int = 7) -> dict | None:
    """Assemble the home-footprint panel, or None when there's no mobility data
    (single-sensor / unlabelled home, or features not built yet)."""
    from ..features.registry import active_feature_set_version
    try:
        fset = active_feature_set_version(repo)
    except Exception:
        return None
    end = datetime.now(timezone.utc)
    lookback = max(int(days or 7), 14)
    try:
        feats = tsdb.read_features(person_id, fset, end - timedelta(days=lookback), end)
    except Exception:
        log.debug("footprint: read_features failed", exc_info=True)
        return None
    if feats is None or feats.empty or not any(c in feats.columns for c in _MOB):
        return None

    disp = feats[feats.index >= (end - timedelta(days=days))]
    now = _agg(disp if not disp.empty else feats)
    if now is None:
        return None

    # gentle WoW trend on roaming: last 7 days vs the prior 7
    trend = None
    wk = _agg(feats[feats.index >= end - timedelta(days=7)])
    prev = _agg(feats[(feats.index >= end - timedelta(days=14))
                      & (feats.index < end - timedelta(days=7))])
    if wk and prev and wk["roaming"] is not None and prev["roaming"] is not None:
        delta = wk["roaming"] - prev["roaming"]
        if abs(delta) >= 0.12:            # only surface a meaningful shift
            trend = "roaming more than last week" if delta > 0 \
                    else "more settled than last week"

    return {
        "rooms": round(now["rooms"], 1) if now["rooms"] is not None else None,
        "roaming": now["roaming"],
        "roaming_label": _roaming_label(now["roaming"]),
        "pacing": now["pacing"],
        "pacing_label": _pacing_label(now["pacing"]),
        "trend": trend,
        "windows": now["windows"],
    }

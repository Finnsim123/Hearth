"""Transition markers — events that mark a CHANGE of state, not activities.

A coffee machine at 07:00 or an alarm firing isn't something you *do* for 30 minutes;
it's a near-instantaneous signal that you're moving from one state to another
(asleep → home). Modelling it as an activity is wrong (no duration, pollutes the
softmax, throws away the timing). So a Marker is NEVER a classifier label
(`excluded_from_model`); instead, when its signal fires in a window it injects a
time-localised prior into the forward filter — boost P(from → to), damp the `from`
self-loop — so the published state switches cleanly at the right window.

The dynamic cousin of a foundational fact: a fact says "this window IS asleep"; a
marker says "right now you're CHANGING from asleep to home". Settings-backed:
  markers -> list[Marker]
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median, pstdev

from pydantic import BaseModel

# Feature suffixes the extractors emit (longest first so "on_frac" wins over "frac").
_SUFFIXES = ["opened_any", "open_count", "on_frac", "on_last", "playing",
             "active", "delta", "mean", "max", "last"]
# How hard a fired marker pulls the prior toward its `to_state`.
BOOST = 6.0
DAMP = 0.3


class Marker(BaseModel):
    slug: str
    name: str
    to_state: str                   # the activity you transition INTO (must exist)
    from_state: str | None = None   # the state you leave (None = any → anchor only)
    binding_name: str               # the sensor whose firing marks the transition
    person_id: str | None = None
    enabled: bool = True
    source: str = "manual"          # manual | discovery
    cluster_id: int | None = None
    excluded_from_model: bool = True  # a marker is NEVER a classifier label
    # lead/lag: minutes the signal LEADS the transition (coffee fires ~30 min before
    # waking → 30). The boost is applied to the window at fire_time + lead_min, so the
    # state flips at the real transition, not when the appliance fires. 0 = coincident.
    lead_min: int = 0
    # 0..1 reliability weight that scales the boost: a timer-like signal that often
    # fires WITHOUT the transition (low precision) or with a wobbly lag is demoted to a
    # gentle hint instead of a hard flip. Learned by learn_marker_timing.
    strength: float = 1.0
    timing: dict | None = None      # last learned estimate (lead/spread/precision), for the UI


# ── persistence (settings) ───────────────────────────────────────────────────
def load_markers(repo) -> list[Marker]:
    raw = repo.get_setting("markers") or []
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(Marker(**d))
        except Exception:
            continue
    return out


def save_markers(repo, markers: list[Marker]) -> None:
    repo.set_setting("markers", [m.model_dump(mode="json") for m in markers])


def markers_for(repo, person_id: str) -> list[Marker]:
    return [m for m in load_markers(repo)
            if m.enabled and m.person_id in (None, person_id)]


# ── signal → binding ─────────────────────────────────────────────────────────
def binding_from_feature(feature: str) -> str:
    """Strip the extractor suffix off a feature name to recover the binding name,
    e.g. 'coffee_delta' -> 'coffee', 'lamp_on_frac' -> 'lamp'. Binding names may
    contain underscores, so match known suffixes rather than splitting."""
    for suf in _SUFFIXES:
        if feature.endswith("_" + suf):
            return feature[: -(len(suf) + 1)]
    return feature


def marker_fired(feat_row, marker: Marker) -> bool:
    """Did the marker's signal fire in this window? Reads whatever feature columns
    exist for the binding: an 'on'/edge signal, an opened flag, or a positive
    cumulative delta (steps/power increase)."""
    name = marker.binding_name
    for suf, thresh in (("on_frac", 0.3), ("on_last", 0.5), ("opened_any", 0.5),
                        ("active", 0.5), ("playing", 0.5), ("max", 0.5)):
        col = f"{name}_{suf}"
        try:
            if col in feat_row and float(feat_row[col]) > thresh:
                return True
        except (TypeError, ValueError):
            continue
    for suf in ("delta", "open_count"):
        col = f"{name}_{suf}"
        try:
            if col in feat_row and float(feat_row[col]) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def apply_marker_prior(row, prev_state, fired: list[Marker], *,
                       boost: float = BOOST, damp: float = DAMP):
    """Re-weight a per-window probability Series given the markers that fired.
    For each fired marker whose `from` matches `prev_state` (or is None), boost the
    `to_state` and damp the `from` self-loop, then renormalise. Returns `row`
    unchanged if nothing applies (pure; row is a pandas Series)."""
    out = row.copy()
    changed = False
    for m in fired:
        if m.from_state not in (None, prev_state):
            continue
        if m.to_state not in out.index:
            continue
        # scale by the marker's learned reliability: weak/timer-like → gentle hint
        s = max(0.0, min(1.0, getattr(m, "strength", 1.0)))
        eff_boost = 1.0 + (boost - 1.0) * s
        eff_damp = 1.0 - (1.0 - damp) * s
        out[m.to_state] = float(out[m.to_state]) * eff_boost + 1e-6
        if prev_state in out.index and prev_state != m.to_state:
            out[prev_state] = float(out[prev_state]) * eff_damp
        changed = changed or s > 0
    if not changed:
        return row
    total = float(out.sum())
    return out / total if total > 0 else row


# ── discovery heuristic ──────────────────────────────────────────────────────
def looks_like_marker(hour_histogram: list[int], n_windows: int) -> bool:
    """A marker-like cluster is brief and time-concentrated: most of its windows
    fall in a narrow band of the day, and there aren't many of them. Used to
    pre-suggest 'a moment of change' when naming a discovered pattern."""
    total = sum(hour_histogram or [])
    if total < 3:
        return False
    peak = max(hour_histogram)
    top2 = sum(sorted(hour_histogram, reverse=True)[:2])
    concentrated = (peak / total) >= 0.4 or (top2 / total) >= 0.6
    return concentrated and n_windows <= 8


# ── lead/lag learning ────────────────────────────────────────────────────────
def estimate_lag(fire_ts: list[datetime], transition_ts: list[datetime], *,
                 max_lag_min: int = 90, window_min: int = 30,
                 tol_min: int = 15) -> dict:
    """Cross-correlate a marker's fire times with the actual from→to transitions.
    Returns the modal lead (minutes the fire precedes the transition, rounded to the
    window), its spread, and precision/recall:
      precision = fires that were followed by the transition / all fires
                  (low → a timer that fires without the transition — don't trust it)
      recall    = transitions preceded by a fire / all transitions
    """
    fires, trans = sorted(fire_ts), sorted(transition_ts)
    base = {"lead_min": 0, "spread_min": 0.0, "precision": 0.0, "recall": 0.0,
            "n_fires": len(fires), "n_transitions": len(trans)}
    if not fires or not trans:
        return base
    lags, hit_fires = [], 0
    for f in fires:
        lo, hi = f - timedelta(minutes=tol_min), f + timedelta(minutes=max_lag_min)
        cand = [t for t in trans if lo <= t <= hi]
        if cand:
            t = min(cand, key=lambda t: abs((t - f).total_seconds()))
            lags.append((t - f).total_seconds() / 60.0)
            hit_fires += 1
    matched = sum(1 for t in trans
                  if any(t - timedelta(minutes=max_lag_min) <= f <= t + timedelta(minutes=tol_min)
                         for f in fires))
    lead_min = int(round(median(lags) / window_min)) * window_min if lags else 0
    spread = round(pstdev(lags), 1) if len(lags) > 1 else 0.0
    return {**base, "lead_min": lead_min, "spread_min": spread,
            "precision": round(hit_fires / len(fires), 3),
            "recall": round(matched / len(trans), 3)}


def strength_from(estimate: dict, window_min: int = 30) -> float:
    """Reliability weight (0..1) from an estimate: trust precision, dampened when the
    lag is wobbly (spread beyond one window)."""
    p = max(0.0, min(1.0, estimate.get("precision", 0.0)))
    spread = estimate.get("spread_min", 0.0)
    sf = 1.0 if spread <= window_min else max(0.3, 1.0 - (spread - window_min) / (3 * window_min))
    return round(p * sf, 3)


def _fire_windows(feats, marker: Marker) -> list[datetime]:
    if feats is None or getattr(feats, "empty", True):
        return []
    out = []
    for ts in feats.index:
        try:
            if marker_fired(feats.loc[ts], marker):
                out.append(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts)
        except Exception:
            continue
    return out


def _transition_windows(rows: list[dict], marker: Marker) -> list[datetime]:
    """Windows where the published state crossed this marker's from→to boundary."""
    from .summary import UNKNOWN, _parse, _state
    seq = sorted(((_parse(r["time"]), _state(r)) for r in rows if r.get("time")),
                 key=lambda x: x[0])
    out = []
    for i in range(1, len(seq)):
        prev, cur = seq[i - 1][1], seq[i][1]
        if cur == UNKNOWN:
            continue
        if cur == marker.to_state and marker.from_state in (None, prev) and prev != cur:
            out.append(seq[i][0])
    return out


def learn_marker_timing(repo, tsdb, *, days: int = 30) -> None:
    """For each marker, learn its lead/lag and reliability from recent history and
    persist (lead_min, strength, timing). Cheap daily job; no-op without data."""
    if tsdb is None:
        return
    from datetime import timezone

    from .features.registry import active_feature_set_version
    fset = active_feature_set_version(repo)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    persons = []
    try:
        persons = [p.id for p in repo.persons()]
    except Exception:
        persons = []
    markers = load_markers(repo)
    changed = False
    for m in markers:
        pid = m.person_id or (persons[0] if persons else "")
        try:
            feats = tsdb.read_features(pid, fset, start, end)
            preds = tsdb.read_predictions(pid, start, end)
        except Exception:
            continue
        est = estimate_lag(_fire_windows(feats, m), _transition_windows(preds, m))
        if est["n_fires"] == 0 or est["n_transitions"] == 0:
            continue
        m.lead_min = est["lead_min"]
        m.strength = strength_from(est)
        m.timing = est
        changed = True
    if changed:
        save_markers(repo, markers)

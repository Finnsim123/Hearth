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
        out[m.to_state] = float(out[m.to_state]) * boost + 1e-6
        if prev_state in out.index and prev_state != m.to_state:
            out[prev_state] = float(out[prev_state]) * damp
        changed = True
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

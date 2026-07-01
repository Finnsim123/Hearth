"""Activity colour palette — one source of truth for auto-assigned colours.

Every activity carries a `color` used consistently across the whole UI
(dashboard, behaviour, inbox…). New activities auto-pick the first palette
colour not already in use, so a fresh taxonomy is visually distinct without the
user touching anything. Well-known slugs keep their historical hues (matches
theme.css) so the standard preset looks the same as before.
"""
from __future__ import annotations

# The sentinel default from schemas.Activity.color — treated as "unset", so
# auto-assignment kicks in. A user who genuinely wants grey can pick a near-grey
# from the picker; pure #888888 means "not chosen yet".
DEFAULT_COLOR = "#888888"

# Ordered palette — distinct, accessible hues. First seven mirror the legacy
# --act-* CSS vars so existing homes don't see their colours shuffle.
PALETTE: list[str] = [
    "#34d399",  # green
    "#818cf8",  # indigo
    "#f59e0b",  # amber
    "#f472b6",  # pink
    "#60a5fa",  # blue
    "#fb923c",  # orange
    "#22d3ee",  # cyan
    "#a78bfa",  # violet
    "#2dd4bf",  # teal
    "#facc15",  # yellow
    "#fb7185",  # rose
    "#c084fc",  # purple
    "#4ade80",  # lime
    "#94a3b8",  # slate
]

# Historical hues for the standard taxonomy (from theme.css --act-*), so the
# familiar set keeps its identity regardless of insertion order.
WELL_KNOWN: dict[str, str] = {
    "sleeping": "#818cf8", "asleep": "#818cf8",
    "away": "#94a3b8",
    "home": "#34d399",
    "cooking": "#f59e0b",
    "eating": "#fb923c",
    "movie": "#f472b6", "media": "#f472b6",
    "working": "#60a5fa",
}


def is_unset(color: str | None) -> bool:
    """True when a colour should be auto-assigned (blank or the grey sentinel)."""
    c = (color or "").strip().lower()
    return c in ("", DEFAULT_COLOR)


def pick_color(slug: str, used: set[str]) -> str:
    """Choose a colour for `slug`: its historical hue if well-known, else the
    first palette colour not already taken, else a stable palette slot by slug
    hash (deterministic, so it never changes across restarts)."""
    wk = WELL_KNOWN.get(slug)
    if wk:
        return wk
    used_l = {c.strip().lower() for c in used}
    for c in PALETTE:
        if c.lower() not in used_l:
            return c
    return PALETTE[sum(ord(ch) for ch in slug) % len(PALETTE)]


def ensure_colors(repo) -> int:
    """Backfill any activity left on the sentinel colour (e.g. seeded before the
    palette existed). Idempotent; returns how many were recoloured."""
    try:
        acts = list(repo.activities())
    except Exception:
        return 0
    used = {a.color for a in acts if not is_unset(a.color)}
    changed = 0
    for a in acts:
        if is_unset(a.color):
            a.color = pick_color(a.slug, used)
            used.add(a.color)
            repo.save_activity(a)
            changed += 1
    return changed

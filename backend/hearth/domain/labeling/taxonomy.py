"""Activity hierarchy — coarse states and the fine activities inside them.

Research grounding (docs/RESEARCH.md §Hierarchy):
  * Coarse states (sleeping / home / away) are mutually exclusive, data-rich,
    and easy; fine activities (cooking, eating, movie, working) are hard and
    only exist INSIDE a coarse state — "home and eating" are simultaneously
    true because eating is a child of home, not a competitor.
  * The literature's answer is LCPN — Local Classifier Per Parent Node
    (HHAR-net and others): a root classifier picks the coarse state, and a
    separate classifier per parent distinguishes only that parent's children.
    Prediction is top-down, so output is always a consistent PATH.
  * Why it wins here: the root model keeps training on every window (tons of
    bootstrap labels — stays accurate), while each child model trains only on
    windows with fine labels under its parent — a small but focused dataset
    that never dilutes coarse accuracy. Confidence is per level, so Hearth
    can be sure you're HOME while asking whether you're cooking or eating.

The hierarchy is DATA: Activity.parent_id (two levels). Everything here
derives from the activities table — nothing hardcoded.
"""
from __future__ import annotations

from ..schemas import Activity

# A child model needs at least this many fine-labeled windows to train.
MIN_CHILD_WINDOWS = 60


def parent_map(activities: list[Activity]) -> dict[str, str | None]:
    """{slug: parent_slug|None} from the activities table."""
    by_id = {a.id: a.slug for a in activities if a.id is not None}
    return {a.slug: by_id.get(a.parent_id) for a in activities}


def to_coarse(label: str, pmap: dict[str, str | None]) -> str:
    """Any label → its top-level ancestor ("eating" → "home")."""
    seen = set()
    while pmap.get(label) is not None and label not in seen:
        seen.add(label)
        label = pmap[label]  # type: ignore[assignment]
    return label


def children_of(parent_slug: str, activities: list[Activity]) -> list[str]:
    pmap = parent_map(activities)
    return [a.slug for a in activities if pmap.get(a.slug) == parent_slug]


def parents_with_children(activities: list[Activity]) -> list[str]:
    """Coarse slugs that have fine activities under them (need a child model)."""
    pmap = parent_map(activities)
    return sorted({p for p in pmap.values() if p is not None})


def fine_label_series(labels, parent_slug: str, pmap: dict[str, str | None]):
    """Project a label series onto one parent's sub-problem:
    children keep their slug; windows labeled exactly `parent_slug` become the
    'unspecified' class (= the parent slug itself); everything else is NaN
    (not this parent's business). Pandas-free typing: works on pd.Series."""
    def project(lab: str):
        if lab == parent_slug:
            return parent_slug              # "just home" — the abstain class
        if to_coarse(lab, pmap) == parent_slug and pmap.get(lab) is not None:
            return lab
        return None
    return labels.map(project)


# Standard-preset fine activities live under "home". Used at setup AND as a
# startup backfill so existing installs get the hierarchy without re-seeding.
DEFAULT_FINE_UNDER_HOME = {"cooking", "eating", "movie", "working", "chilling"}


def ensure_hierarchy(repo) -> int:
    """Idempotent: give known fine slugs their parent if they're orphaned."""
    activities = repo.activities()
    home = next((a for a in activities if a.slug == "home"), None)
    if home is None or home.id is None:
        return 0
    fixed = 0
    for a in activities:
        if a.slug in DEFAULT_FINE_UNDER_HOME and a.parent_id is None:
            a.parent_id = home.id
            repo.save_activity(a)
            fixed += 1
    return fixed

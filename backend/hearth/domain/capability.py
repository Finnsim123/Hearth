"""Honest capability — turn the metrics we already compute into a plain verdict of
what the model can and can't actually do, per activity, with an actionable remedy.

The product principle: never show an action without its quality. This is the engine
behind "with these sensors I can't tell cooking from eating — add a stove sensor."

Pure: metrics + coverage gaps in, a CapabilityReport out. No I/O.
"""
from __future__ import annotations

from pydantic import BaseModel

# Balanced thresholds (one place to tune; see honest_capability_design.md §7).
F1_RELIABLE = 0.70
F1_UNRELIABLE = 0.50
CONFUSE_MAX = 0.40        # a >40% mutual confusion pair = it genuinely can't separate them
MIN_GOLD = 30            # enough real-world spot-checks to judge at all
MIN_SUPPORT = 10         # enough validation examples of THIS activity to grade it

RELIABLE, LEARNING, UNRELIABLE, BLIND = "reliable", "learning", "unreliable", "blind"


class ActivityCapability(BaseModel):
    slug: str
    name: str
    tier: str                       # reliable | learning | unreliable | blind
    reason: str
    remedy: str | None = None
    f1: float | None = None
    support: int = 0
    confused_with: str | None = None


class CapabilityReport(BaseModel):
    person_id: str
    has_model: bool
    validation_status: str          # validated | provisional | none
    overall: str
    reliable: list[str]
    needs_help: list[str]           # unreliable + blind slugs (what to act on)
    activities: list[ActivityCapability]


def _top_confusion(confusion: dict, slug: str) -> tuple[str | None, float]:
    """The activity `slug` is most confused with, and the symmetric confusion rate."""
    labels = confusion.get("labels") or []
    matrix = confusion.get("matrix") or []
    if slug not in labels or len(matrix) != len(labels):
        return None, 0.0
    i = labels.index(slug)
    support = [sum(matrix[r]) for r in range(len(labels))]
    best, best_rate = None, 0.0
    for j in range(len(labels)):
        if j == i:
            continue
        denom = support[i] + support[j]
        if denom <= 0:
            continue
        rate = (matrix[i][j] + matrix[j][i]) / denom
        if rate > best_rate:
            best, best_rate = labels[j], rate
    return best, round(best_rate, 3)


def _beats_flat(metrics: dict) -> bool:
    fb = metrics.get("flat_baseline") or {}
    mine = metrics.get("accuracy_gold") or metrics.get("accuracy_confirmed")
    flat = fb.get("accuracy_gold") or fb.get("accuracy_confirmed")
    if mine is None or flat is None:
        return True                 # can't compare → don't penalise
    return mine >= flat


def _remedy(a: str, b: str | None, gaps) -> str | None:
    """Pull a concrete 'add sensor' sentence from the coverage advisor for this
    activity / confusable pair, else a generic one."""
    pair = {a, b} if b else {a}
    for g in gaps or []:
        gacts = set(getattr(g, "activities", None) or [])
        if gacts and (gacts == pair or a in gacts):
            return getattr(g, "recommendation", None)
    if b:
        return (f"They happen with the same sensors, so I can't separate them. "
                f"Add a sensor that distinguishes {a} from {b} (or merge them if "
                f"they're really the same).")
    return f"Add a sensor where {a} happens — there isn't enough signal to recognise it."


def assess_capability(person_id: str, activities: list, metrics: dict | None,
                      coverage_gaps=None, *, name_of=None) -> CapabilityReport:
    """Grade each activity honestly. `activities`: objects/dicts with slug (+name).
    `metrics`: the promoted ROOT model metrics, or None. `coverage_gaps`: SensorGap
    list (for remedies + blind rooms)."""
    coverage_gaps = coverage_gaps or []
    name_of = name_of or (lambda s: s)

    def slug_of(a):
        return a["slug"] if isinstance(a, dict) else a.slug

    def disp(a):
        return (a.get("name") if isinstance(a, dict) else getattr(a, "name", None)) or name_of(slug_of(a))

    slugs = [(slug_of(a), disp(a)) for a in activities]

    if not metrics or not metrics.get("per_class"):
        items = [ActivityCapability(slug=s, name=n, tier=LEARNING,
                                    reason="no validated model yet — still learning")
                 for s, n in slugs]
        return CapabilityReport(person_id=person_id, has_model=bool(metrics),
                                validation_status="none" if not metrics else "provisional",
                                overall="Still learning your home — no reliable predictions yet.",
                                reliable=[], needs_help=[], activities=items)

    per_class = metrics.get("per_class") or {}
    confusion = metrics.get("confusion") or {}
    vstatus = metrics.get("validation_status", "provisional")
    n_gold = int(metrics.get("n_gold", 0) or 0)
    judgeable = vstatus == "validated" or n_gold >= MIN_GOLD
    beats = _beats_flat(metrics)

    # blind rooms → activities we can't even attempt (ghost_room coverage gaps that
    # name an activity); kept conservative since room→activity mapping is partial.
    blind_acts = set()
    for g in coverage_gaps:
        if getattr(g, "kind", "") == "ghost_room":
            for s in (getattr(g, "activities", None) or []):
                blind_acts.add(s)

    items: list[ActivityCapability] = []
    for s, n in slugs:
        if s in blind_acts:
            items.append(ActivityCapability(slug=s, name=n, tier=BLIND,
                reason="no sensor covers where this happens",
                remedy=_remedy(s, None, coverage_gaps)))
            continue
        pc = per_class.get(s)
        if not pc:
            items.append(ActivityCapability(slug=s, name=n, tier=LEARNING,
                reason="not seen enough yet to judge", support=0))
            continue
        f1 = float(pc.get("f1", 0.0))
        support = int(pc.get("support", 0))
        partner, crate = _top_confusion(confusion, s)
        if support < MIN_SUPPORT or not judgeable:
            items.append(ActivityCapability(slug=s, name=n, tier=LEARNING, f1=f1,
                support=support, reason=f"still gathering examples ({support} so far)"))
        elif f1 >= F1_RELIABLE and beats and crate <= CONFUSE_MAX:
            items.append(ActivityCapability(slug=s, name=n, tier=RELIABLE, f1=f1,
                support=support, reason=f"right about {round(f1 * 100)}% of the time"))
        elif crate > CONFUSE_MAX:
            items.append(ActivityCapability(slug=s, name=n, tier=UNRELIABLE, f1=f1,
                support=support, confused_with=partner,
                reason=f"keeps getting mixed up with {name_of(partner)} ({round(crate * 100)}%)",
                remedy=_remedy(s, partner, coverage_gaps)))
        elif f1 < F1_UNRELIABLE or not beats:
            items.append(ActivityCapability(slug=s, name=n, tier=UNRELIABLE, f1=f1,
                support=support,
                reason=("no better than a blind guess here" if not beats
                        else f"only right about {round(f1 * 100)}% of the time"),
                remedy=_remedy(s, None, coverage_gaps)))
        else:
            items.append(ActivityCapability(slug=s, name=n, tier=LEARNING, f1=f1,
                support=support, reason="getting there — not reliable yet"))

    reliable = [it.slug for it in items if it.tier == RELIABLE]
    needs = [it.slug for it in items if it.tier in (UNRELIABLE, BLIND)]
    rel_names = [it.name for it in items if it.tier == RELIABLE]
    need_names = [it.name for it in items if it.tier in (UNRELIABLE, BLIND)]
    if reliable and needs:
        overall = f"Reliable: {', '.join(rel_names)}. Can't do well yet: {', '.join(need_names)}."
    elif reliable:
        overall = f"Reliable: {', '.join(rel_names)}."
    elif needs:
        overall = f"Not working yet: {', '.join(need_names)} — see what would help."
    else:
        overall = "Still learning — nothing validated yet."
    return CapabilityReport(person_id=person_id, has_model=True, validation_status=vstatus,
                            overall=overall, reliable=reliable, needs_help=needs, activities=items)

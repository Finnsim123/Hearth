"""Blind-spot advisor — 'add a sensor in the kitchen, I can't see clearly there'.

Pure functions over things the system already computes:
- confusion matrix (ModelRecord.metrics['confusion'])  -> which activities blur
- per-activity room (from cluster signatures / importances)  -> where they happen
- per-activity ambient share (evidence profile)  -> is it guessing from weak signal
- bindings (role, room)  -> what the home can actually sense, and where

Output: ranked SensorGap recommendations. Detection is statistical and LLM-free;
`phrase_gap` gives a deterministic sentence, or pass the structured gap to the LLM
for nicer wording. No I/O here — callers assemble the inputs from repo/tsdb.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..schemas import Binding, Role

# what a sensor of each role lets the model SEE, for phrasing + suggestion
_ROLE_PHRASE = {
    Role.POWER: "a smart plug / power sensor on the appliance",
    Role.PRESENCE: "a motion or presence sensor",
    Role.DOOR: "a door/contact sensor",
    Role.ENV: "a temperature, CO2 or humidity sensor",
    Role.MEDIA: "a media/TV state sensor",
    Role.LIGHT: "a light-level sensor",
}
# preference order when suggesting what to ADD to a room that's missing direct signal
_SUGGEST_ORDER = [Role.PRESENCE, Role.POWER, Role.DOOR, Role.ENV]


class SensorGap(BaseModel):
    """One actionable blind spot. `severity` (0..1) ranks the list; `suggested_role`
    is the sensor kind most likely to help; `recommendation` is the phrased advice."""

    kind: str                       # confused_pair | weak_evidence | ghost_room
    severity: float
    room: str | None = None
    activities: list[str] = []
    suggested_role: Role | None = None
    detail: str = ""               # machine-readable reason
    recommendation: str = ""       # human sentence (deterministic default)


def room_roles(bindings: list[Binding]) -> dict[str, set[Role]]:
    """room -> set of sensor roles present (enabled bindings only)."""
    out: dict[str, set[Role]] = {}
    for b in bindings:
        if getattr(b, "enabled", True) and b.room:
            out.setdefault(b.room, set()).add(b.role)
    return out


def confused_pairs(confusion: dict, min_rate: float = 0.15) -> list[tuple[str, str, float]]:
    """Symmetric confusion rate per activity pair, descending. rate =
    (M[a→b]+M[b→a]) / (support_a + support_b). Pairs below `min_rate` are dropped."""
    labels = confusion.get("labels") or []
    matrix = confusion.get("matrix") or []
    n = len(labels)
    if n < 2 or len(matrix) != n:
        return []
    support = [sum(matrix[i]) for i in range(n)]
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            denom = support[i] + support[j]
            if denom <= 0:
                continue
            rate = (matrix[i][j] + matrix[j][i]) / denom
            if rate >= min_rate:
                pairs.append((labels[i], labels[j], round(rate, 3)))
    return sorted(pairs, key=lambda p: -p[2])


def _suggest_role(present: set[Role]) -> Role | None:
    """The most useful direct-signal role a room is missing."""
    for r in _SUGGEST_ORDER:
        if r not in present:
            return r
    return None


def detect_gaps(
    confusion: dict,
    activity_room: dict[str, str],
    activity_ambient_share: dict[str, float],
    bindings: list[Binding],
    *,
    min_confusion: float = 0.15,
    weak_ambient: float = 0.5,
    referenced_rooms: set[str] | None = None,
) -> list[SensorGap]:
    """Rank blind spots. Three detectors, deduped by (kind, room, activities):

    1. confused_pair  — two activities blur AND share a room that lacks a
       discriminating direct sensor → suggest one.
    2. weak_evidence  — an activity leans on ambient signal (high ambient share)
       → suggest a direct sensor in its room.
    3. ghost_room     — a room activities happen in has NO sensor at all.
    """
    rr = room_roles(bindings)
    gaps: list[SensorGap] = []

    for a, b, rate in confused_pairs(confusion, min_confusion):
        ra, rb = activity_room.get(a), activity_room.get(b)
        room = ra if ra and ra == rb else (ra or rb)
        present = rr.get(room, set()) if room else set()
        role = _suggest_role(present)
        gaps.append(SensorGap(
            kind="confused_pair", severity=round(min(1.0, rate * 1.5), 3),
            room=room, activities=[a, b], suggested_role=role,
            detail=f"confused {rate:.0%}; room={room or '?'} has roles "
                   f"{sorted(r.value for r in present) or 'none'}"))

    for act, share in sorted(activity_ambient_share.items(), key=lambda kv: -kv[1]):
        if share < weak_ambient:
            continue
        room = activity_room.get(act)
        present = rr.get(room, set()) if room else set()
        role = _suggest_role(present)
        gaps.append(SensorGap(
            kind="weak_evidence", severity=round(min(1.0, share), 3),
            room=room, activities=[act], suggested_role=role,
            detail=f"{share:.0%} of evidence is ambient/indirect"))

    for room in sorted(referenced_rooms or set()):
        if room not in rr or not rr.get(room):
            gaps.append(SensorGap(
                kind="ghost_room", severity=0.6, room=room,
                suggested_role=Role.PRESENCE,
                detail=f"no usable sensor in {room}"))

    for g in gaps:
        g.recommendation = phrase_gap(g)
    # dedupe + rank
    seen, out = set(), []
    for g in sorted(gaps, key=lambda g: -g.severity):
        key = (g.kind, g.room, tuple(sorted(g.activities)))
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


def _device_rooms(repo) -> set[str]:
    """Rooms that contain at least one HA device (from the cached catalog) — a real,
    used room, so a blind spot there is worth flagging."""
    try:
        from ..hierarchy import load_device_catalog
        return {v.get("area") for v in load_device_catalog(repo).values() if v.get("area")}
    except Exception:
        return set()


def gaps_from_home(repo) -> list[SensorGap]:
    """Assemble inputs from app state and rank blind spots across the household.

    Grounded in what's available today: confused pairs from each promoted ROOT
    model's confusion matrix, against the home's room/role coverage. Per-activity
    room and ambient-share enrichment (from named cluster evidence / evidence
    profile) is a follow-up; until then confused-pair advice is room-agnostic and
    ghost-room advice uses the binding room set.
    """
    try:
        bindings = repo.bindings()
    except Exception:
        return []
    binding_rooms = {b.room for b in bindings if getattr(b, "room", None)}
    device_rooms = _device_rooms(repo)          # rooms that actually contain devices
    referenced = binding_rooms | device_rooms
    out: list[SensorGap] = []
    seen: set = set()

    # Device-aware ghost rooms — works even before any model exists: a room that
    # HAS devices but no sensor Hearth can use to see activity there.
    rr = room_roles(bindings)
    for room in sorted(device_rooms):
        if room and not rr.get(room):
            g = SensorGap(kind="ghost_room", severity=0.6, room=room,
                          suggested_role=Role.PRESENCE,
                          detail=f"devices in {room} but nothing Hearth can use")
            g.recommendation = (
                f"You have devices in the {room} but nothing Hearth can use to see "
                f"activity there — bind one on the Sensors page, or add a motion sensor.")
            out.append(g)
            seen.add(("ghost_room", room, ()))

    persons = []
    try:
        persons = repo.persons()
    except Exception:
        persons = []
    for p in persons:
        try:
            roots = [m for m in repo.models(p.id)
                     if getattr(m, "promoted", False) and getattr(m, "node", "root") == "root"]
        except Exception:
            continue
        for m in roots:
            conf = (getattr(m, "metrics", None) or {}).get("confusion")
            if not conf:
                continue
            for g in detect_gaps(conf, {}, {}, bindings, referenced_rooms=referenced):
                key = (g.kind, g.room, tuple(sorted(g.activities)))
                if key in seen:
                    continue
                seen.add(key)
                out.append(g)
    return sorted(out, key=lambda g: -g.severity)


def phrase_gap(g: SensorGap) -> str:
    """Deterministic plain-English advice. Pass the structured gap to the LLM for
    warmer wording; this is the no-LLM default and the test anchor."""
    role_phrase = _ROLE_PHRASE.get(g.suggested_role, "another sensor") \
        if g.suggested_role else "another sensor"
    where = f"the {g.room}" if g.room else "that area"
    if g.kind == "confused_pair" and len(g.activities) == 2:
        a, b = g.activities
        return (f"I keep mixing up {a} and {b}, both around {where}. "
                f"Adding {role_phrase} there would help me tell them apart.")
    if g.kind == "weak_evidence" and g.activities:
        return (f"I recognise {g.activities[0]} mostly from indirect signals — "
                f"{role_phrase} in {where} would make it reliable.")
    if g.kind == "ghost_room":
        return f"I'm blind in {where}. {role_phrase.capitalize()} there would help."
    return f"Consider adding {role_phrase} in {where}."

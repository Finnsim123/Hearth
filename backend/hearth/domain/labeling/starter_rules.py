"""Starter labeling rules generated from BINDINGS — zero entity names in code.

The taxonomy presets give classes; this gives them day-one labels. Templates
are keyed on roles and instantiated with each home's binding names, so the
same generator works for every house. All generated rules are editable data
(origin='user', visible in the Activities page) — the user owns them.

Priority bands: away 10 < sleeping 20-29 < cooking 30 < movie 40 < eating 50.
"""
from __future__ import annotations

from ..schemas import Activity, Binding, Role, Rule


def _bindings_by_role(bindings: list[Binding], role: Role,
                      person_id: str | None = None) -> list[Binding]:
    return [b for b in bindings if b.enabled and b.role == role
            and (person_id is None or b.person_id in (None, person_id))]


def starter_rules(bindings: list[Binding], activities: list[Activity],
                  person_id: str | None = None) -> list[Rule]:
    """Generate rules for one person (or shared when person_id None).
    Only emits a rule when (a) the activity exists and (b) the home has the
    sensors to support it."""
    slugs = {a.slug for a in activities if a.enabled}
    out: list[Rule] = []

    def night_or(*extra) -> dict:
        node = {"any": [{"feat": "hour_of_day", "op": ">=", "value": 22},
                        {"feat": "hour_of_day", "op": "<", "value": 7}, *extra]}
        return node

    # away — person's own location binding says not home
    if "away" in slugs:
        for b in _bindings_by_role(bindings, Role.PERSON, person_id):
            if person_id and b.person_id != person_id:
                continue
            out.append(Rule(activity_slug="away", person_id=b.person_id,
                            priority=10,
                            predicate={"all": [{"feat": f"{b.name}_home_last",
                                                "op": "==", "value": 0}]}))

    # sleeping — bed occupied during night hours; person-specific bed first
    if "sleeping" in slugs:
        for i, b in enumerate(_bindings_by_role(bindings, Role.BED, person_id)):
            out.append(Rule(activity_slug="sleeping", person_id=b.person_id or person_id,
                            priority=20 + i,
                            predicate={"all": [
                                {"feat": f"{b.name}_occupied", "op": "==", "value": 1},
                                night_or()]}))
        # focus/DND at night as a weaker fallback when no bed sensor exists
        beds = _bindings_by_role(bindings, Role.BED, person_id)
        if not beds:
            for b in _bindings_by_role(bindings, Role.FOCUS, person_id):
                out.append(Rule(activity_slug="sleeping", person_id=b.person_id or person_id,
                                priority=28,
                                predicate={"all": [
                                    {"feat": f"{b.name}_on_last", "op": "==", "value": 1},
                                    night_or()]}))

    # cooking — kitchen-ish presence + power spike from a kitchen power binding
    if "cooking" in slugs:
        kitchen_presence = [b for b in _bindings_by_role(bindings, Role.PRESENCE, person_id)
                            if "kitchen" in (b.room or b.name).lower()]
        kitchen_power = [b for b in _bindings_by_role(bindings, Role.POWER, person_id)
                         if any(k in (b.room or b.name).lower()
                                for k in ("kitchen", "oven", "stove", "cook"))]
        for p in kitchen_presence:
            conds = [{"feat": f"{p.name}_frac", "op": ">", "value": 0.3}]
            if kitchen_power:
                conds.append({"feat": f"{kitchen_power[0].name}_on", "op": "==", "value": 1})
            out.append(Rule(activity_slug="cooking", person_id=person_id,
                            priority=30, predicate={"all": conds}))

    # movie — media playing + a living-area presence binding
    if "movie" in slugs:
        media = _bindings_by_role(bindings, Role.MEDIA, person_id)
        presence = [b for b in _bindings_by_role(bindings, Role.PRESENCE, person_id)
                    if any(k in (b.room or b.name).lower()
                           for k in ("living", "sofa", "couch", "tv"))]
        for m in media:
            conds = [{"feat": f"{m.name}_playing", "op": "==", "value": 1}]
            if presence:
                conds.append({"feat": f"{presence[0].name}_frac", "op": ">", "value": 0.2})
            out.append(Rule(activity_slug="movie", person_id=person_id,
                            priority=40, predicate={"all": conds}))

    # eating — dining presence (when such a room exists)
    if "eating" in slugs:
        dining = [b for b in _bindings_by_role(bindings, Role.PRESENCE, person_id)
                  if any(k in (b.room or b.name).lower() for k in ("dining", "table"))]
        for d in dining:
            out.append(Rule(activity_slug="eating", person_id=person_id,
                            priority=50,
                            predicate={"all": [{"feat": f"{d.name}_frac",
                                                "op": ">", "value": 0.3}]}))
    return out

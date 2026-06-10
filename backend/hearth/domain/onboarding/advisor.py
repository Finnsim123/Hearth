"""Onboarding suggestion service — heuristics always, LLM when configured.

heuristic_bindings maps HA entity METADATA (domain, device_class, unit, name
words) to Roles. Generic vocabulary only — these are HA conventions, not any
particular home. The LLM advisor (ADR-12) proposes over the same inventory
and wins ties; both are rendered as proposals the user approves.
"""
from __future__ import annotations

import re

from ..ports import LlmAdvisor
from ..schemas import Activity, Binding, Role, Rule

_NAME_HINTS: list[tuple[str, Role]] = [
    (r"bed|mattress|sleep_sensor", Role.BED),
    (r"presence|occupancy|motion|mmwave|pir", Role.PRESENCE),
    (r"power|vermogen|wattage|consumption|_watts?", Role.POWER),
    (r"focus|do_not_disturb|dnd", Role.FOCUS),
    (r"steps", Role.STEPS),
    (r"alarm|wecker|wake", Role.ALARM_TIME),
    (r"door|window|opening|contact", Role.DOOR),
    (r"co2|pm2|pm10|voc|humidity|temperature|lux|illuminance", Role.ENV),
]
_UNIT_ROLES = {"W": Role.POWER, "kW": Role.POWER, "ppm": Role.ENV, "µg/m³": Role.ENV,
               "°C": Role.ENV, "°F": Role.ENV, "%": Role.ENV, "lx": Role.ENV,
               "steps": Role.STEPS, "V": Role.CUSTOM}
_DEVICE_CLASS_ROLES = {"occupancy": Role.PRESENCE, "motion": Role.PRESENCE,
                       "presence": Role.PRESENCE, "door": Role.DOOR, "window": Role.DOOR,
                       "opening": Role.DOOR, "power": Role.POWER, "energy": Role.POWER,
                       "battery": Role.BATTERY, "temperature": Role.ENV,
                       "humidity": Role.ENV, "carbon_dioxide": Role.ENV,
                       "illuminance": Role.ENV, "pm25": Role.ENV, "timestamp": Role.ALARM_TIME}
_DOMAIN_ROLES = {"light": Role.LIGHT, "media_player": Role.MEDIA, "person": Role.PERSON,
                 "device_tracker": Role.PERSON, "input_datetime": Role.ALARM_TIME}


def _slugify(entity_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", entity_id.split(".", 1)[-1].lower()).strip("_")


def suggest_role(entity: dict) -> Role | None:
    """entity: one inventory item (entity_id, domain, device_class, unit, name)."""
    domain = entity.get("domain") or entity["entity_id"].split(".")[0]
    if domain in _DOMAIN_ROLES:
        return _DOMAIN_ROLES[domain]
    dc = entity.get("device_class")
    if dc in _DEVICE_CLASS_ROLES:
        return _DEVICE_CLASS_ROLES[dc]
    text = f"{entity['entity_id']} {entity.get('friendly_name', '')}".lower()
    for pattern, role in _NAME_HINTS:
        if re.search(pattern, text):
            return role
    unit = entity.get("unit")
    if unit in _UNIT_ROLES:
        return _UNIT_ROLES[unit]
    return None


def heuristic_bindings(inventory: list[dict]) -> list[Binding]:
    """Proposed bindings for every entity with a recognizable role; the rest
    are left unbound (user can still bind as CUSTOM)."""
    out: list[Binding] = []
    seen_names: set[str] = set()
    for e in inventory:
        role = suggest_role(e)
        if role is None:
            continue
        name = _slugify(e["entity_id"])
        while name in seen_names:
            name += "_2"
        seen_names.add(name)
        out.append(Binding(entity_id=e["entity_id"], role=role, name=name,
                           room=e.get("area"), enabled=True))
    return out


async def suggest_setup(
    inventory: list[dict], advisor: LlmAdvisor | None,
) -> tuple[list[Binding], list[Activity], list[Rule]]:
    """Wizard entrypoint. advisor=None -> heuristics + empty taxonomy/rules
    (presets cover taxonomy). With an advisor, LLM proposals win ties."""
    heur = {b.entity_id: b for b in heuristic_bindings(inventory)}
    if advisor is None:
        return list(heur.values()), [], []
    llm_bindings = await advisor.propose_bindings(inventory)
    merged = {**heur, **{b.entity_id: b for b in llm_bindings}}
    taxonomy = await advisor.propose_taxonomy(inventory)
    rules = await advisor.propose_rules(list(merged.values()), taxonomy)
    return list(merged.values()), taxonomy, rules

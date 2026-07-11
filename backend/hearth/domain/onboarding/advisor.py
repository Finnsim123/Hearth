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
    (r"bed|mattress|sleep_sensor|slaap|matras", Role.BED),
    (r"presence|occupancy|motion|mmwave|pir|radar|beweging|aanwezig|"
     r"human_present|ld2410|fp2", Role.PRESENCE),
    (r"power|vermogen|wattage|consumption|verbruik|_watts?|kwh|energie|"
     r"\benergy\b|stopcontact|smartplug|stekker", Role.POWER),
    (r"focus|do_not_disturb|dnd|niet_storen", Role.FOCUS),
    (r"steps|stappen|schritte", Role.STEPS),
    (r"alarm|wecker|wekker|wake_?up|alarmtijd", Role.ALARM_TIME),
    (r"door|window|opening|contact|deur|raam|magnet|reed", Role.DOOR),
    (r"co2|pm1|pm2|pm10|voc|tvoc|nox|no2|aqi|air_?quality|luchtkwaliteit|"
     r"humidity|vochtigheid|temperature|temperatuur|lux|illuminance|"
     r"light_?level|lichtsterkte|brightness", Role.ENV),
    # household-occupancy proxies from the network: a router's connected-device
    # count rises when people are home (CUSTOM = numeric mean/max/delta, the
    # model decides the threshold). Generic per-device trackers stay excluded.
    (r"connected_devices|devices_connected|online_devices|device_count|"
     r"num_clients|clients_total|network_clients", Role.CUSTOM),
    # proximity/distance-to-home is a NUMBER, not a home/away state. It belongs
    # to the model as a numeric feature (small distance → home, large → away,
    # threshold learned), NEVER the PERSON role — see _NOT_A_TRACKER below.
    (r"distance|proximity|afstand|nearest", Role.CUSTOM),
]
_UNIT_ROLES = {"W": Role.POWER, "kW": Role.POWER, "mW": Role.POWER, "Wh": Role.POWER,
               "kWh": Role.POWER, "A": Role.POWER, "mA": Role.POWER,
               "ppm": Role.ENV, "ppb": Role.ENV, "µg/m³": Role.ENV, "g/m³": Role.ENV,
               "°C": Role.ENV, "°F": Role.ENV, "%": Role.ENV, "lx": Role.ENV,
               "klx": Role.ENV, "dB": Role.ENV, "steps": Role.STEPS, "V": Role.CUSTOM}
_DEVICE_CLASS_ROLES = {"occupancy": Role.PRESENCE, "motion": Role.PRESENCE,
                       "presence": Role.PRESENCE, "moving": Role.PRESENCE,
                       "vibration": Role.PRESENCE, "door": Role.DOOR, "window": Role.DOOR,
                       "garage_door": Role.DOOR, "opening": Role.DOOR,
                       "power": Role.POWER, "energy": Role.POWER, "current": Role.POWER,
                       "apparent_power": Role.POWER, "reactive_power": Role.POWER,
                       "outlet": Role.POWER, "battery": Role.BATTERY,
                       "temperature": Role.ENV, "humidity": Role.ENV,
                       "carbon_dioxide": Role.ENV, "carbon_monoxide": Role.ENV,
                       "illuminance": Role.ENV, "pm1": Role.ENV, "pm10": Role.ENV,
                       "pm25": Role.ENV, "nitrogen_dioxide": Role.ENV, "ozone": Role.ENV,
                       "volatile_organic_compounds": Role.ENV, "aqi": Role.ENV,
                       "timestamp": Role.ALARM_TIME}
# device_tracker is deliberately ABSENT: homes have dozens of network
# trackers (laptops, cameras, IoT) — only person.* entities mean a human.
_DOMAIN_ROLES = {"light": Role.LIGHT, "media_player": Role.MEDIA, "person": Role.PERSON,
                 "input_datetime": Role.ALARM_TIME}


def _slugify(entity_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", entity_id.split(".", 1)[-1].lower()).strip("_")


# A real home/away tracker emits zone STATES ("home"/"not_home"/"work"); the
# PERSON role + away rule (home_last == 0) assume exactly that. Companion-app
# *distance*/*proximity* entities are NUMBERS — distance 0 means HOME, the
# opposite of what home_last == 0 encodes. They must never back the PERSON role;
# they feed the model as CUSTOM numeric features instead. This guard is the one
# place that decides "is this entity a genuine home/away tracker?".
_NOT_A_TRACKER = re.compile(r"distance|proximity|afstand|nearest|"
                            r"_km\b|_mi\b|_miles?\b|_meters?\b|_metres?\b")


def is_person_tracker(entity_id: str, friendly_name: str = "") -> bool:
    """True only for entities that report home/away (PERSON role). Numeric
    proximity/distance sensors are excluded — they are model features, not
    presence trackers."""
    if entity_id.split(".")[0] not in ("person", "device_tracker"):
        return False
    return not _NOT_A_TRACKER.search(f"{entity_id} {friendly_name}".lower())


# Diagnostics / infrastructure / forecasts — never useful for activity sensing.
_BLOCKLIST = re.compile(
    r"rssi|lqi|signal_quality|signal_strength|packet_loss|ping|uptime|"
    r"cpu|memory|processor|supervisor|core_|watchman|last_updated|last_parse|"
    r"_[1-5]d\b|forecast|regenkans|zonkans|next_dawn|next_dusk|next_noon|"
    r"next_rising|next_setting|battery_plus|daily_energy|spanning|"
    r"print_progress|firmware|update_available|last_response|failed_pings|"
    r"_slope$|_ema_|preset_|regulated_|"
    r"\bhar_|strava|identify|identificeren|trigger_level|trigger_pressure|"
    r"occupied_pressure|unoccupied_pressure|calibrate|opstartgedrag|"
    r"power_on_level|power_on_behavior|niveau_bij_opstarten|wake_word|"
    r"start_up_color|tts_volume|print_bed|nozzle|heatbreak|cooling_fan|"
    # controller/radio board diagnostics — a Zigbee coordinator's own chip
    # temperature is about the dongle, never the room (cpu/core_ already above).
    r"zigbee|coordinator|slzb|zbdongle|conbee|_chip_|chip_temp|mcu|soc_temp|"
    r"board_temp|radio_temp|die_temp|internal_temp|node_temp|esp_temp")
_BLOCK_DEVICE_CLASSES = {"signal_strength", "timestamp", "update", "data_size",
                         "data_rate", "duration", "monetary"}


# Physics whitelist: a role can only come from domains that actually carry
# that kind of STATE. Buttons/scenes/scripts/updates/config-numbers configure
# sensors — they aren't sensors. Applies to heuristics AND LLM proposals.
ROLE_DOMAINS: dict[Role, set[str]] = {
    Role.PRESENCE: {"binary_sensor", "sensor", "input_boolean"},
    Role.BED: {"binary_sensor", "sensor", "input_boolean", "switch"},
    Role.POWER: {"sensor", "switch"},
    Role.LIGHT: {"light", "switch"},
    Role.MEDIA: {"media_player"},
    Role.ENV: {"sensor"},
    Role.PERSON: {"person", "device_tracker"},  # explicit/override only — never auto
    Role.FOCUS: {"binary_sensor", "switch", "input_boolean"},
    Role.ALARM_TIME: {"input_datetime", "sensor", "input_boolean"},
    Role.DOOR: {"binary_sensor", "lock", "cover", "input_boolean"},
    Role.STEPS: {"sensor"},
    Role.BATTERY: {"sensor"},
    Role.CUSTOM: {"sensor", "binary_sensor", "input_boolean", "input_number",
                  "switch", "light", "media_player"},
}
# Domains with NO state stream to window — commands, configs, one-shots.
# This is physics, not taste, and it is never overridable.
_NEVER_DOMAINS = {"button", "scene", "script", "update", "automation",
                  "camera", "remote", "zone", "tts", "persistent_notification"}
# Domains that DO carry state and may back any role via an explicit override
# (LLM-with-reason or user choice in the Sensors page).
_STATE_DOMAINS = {"sensor", "binary_sensor", "input_boolean", "input_number",
                  "switch", "light", "media_player", "lock", "cover", "number",
                  "select", "input_datetime", "person", "device_tracker",
                  "alarm_control_panel", "input_select", "climate", "fan"}


# Hearth's OWN published entities (mqtt_publisher.py + custom_components/hearth):
# the prediction sensors and diagnostics it writes back into HA. It must never
# sense — let alone train on — its own output, or the model learns to predict its
# own predictions (a feedback loop). Matched precisely (known suffixes + the
# standalone sensors) so a user's unrelated "hearth" entity, e.g. a smart
# fireplace, isn't caught; a device literally named "Hearth" (or "Hearth — <p>")
# is the authoritative signal when we have it.
_SELF_SUFFIXES = ("_activity", "_confidence", "_questions", "_override",
                  "_accuracy")
_SELF_EXACT = {"hearth_alive", "hearth_attention"}


def is_hearth_own(entity: dict) -> bool:
    """True for Hearth's own prediction/diagnostic entities
    (sensor.hearth_<person>_activity, …_confidence, …_accuracy,
    switch/select .hearth_<person>_*, binary_sensor.hearth_alive/_attention)."""
    obj = (entity.get("entity_id") or "").split(".", 1)[-1].lower()
    if obj in _SELF_EXACT or (obj.startswith("hearth_") and obj.endswith(_SELF_SUFFIXES)):
        return True
    dev = (entity.get("device") or "").strip().lower()
    return dev == "hearth" or dev.startswith("hearth —") or dev.startswith("hearth -")


def is_bindable(entity_id: str, role: Role, override: bool = False) -> bool:
    """The appealable gate: stateless domains and the diagnostics blocklist
    are hard physics; the role↔domain map is the DEFAULT, overridable when an
    author (LLM with a reason, or the user) explicitly insists."""
    domain = entity_id.split(".")[0]
    if domain in _NEVER_DOMAINS:
        return False
    if _BLOCKLIST.search(entity_id.lower()):
        return False
    if is_hearth_own({"entity_id": entity_id}):     # never bind our own output
        return False
    if domain in ROLE_DOMAINS.get(role, set()):
        return True
    return override and domain in _STATE_DOMAINS


def is_noise(entity: dict) -> bool:
    """Diagnostics / stateless / blocklisted entities that should NEVER be a sensor
    — correct to leave unassigned, so don't surface them for review (RSSI, uptime,
    firmware, buttons, scenes, Hearth's own prediction sensors, …)."""
    eid = entity.get("entity_id", "")
    domain = entity.get("domain") or (eid.split(".")[0] if eid else "")
    if domain in _NEVER_DOMAINS:
        return True
    if is_hearth_own(entity):
        return True
    if _BLOCKLIST.search(f"{eid} {entity.get('friendly_name', '')}".lower()):
        return True
    return entity.get("device_class") in _BLOCK_DEVICE_CLASSES


def suggest_role(entity: dict) -> Role | None:
    """entity: one inventory item (entity_id, domain, device_class, unit, name)."""
    if is_hearth_own(entity):       # our own prediction output is never a feature
        return None
    text_all = f"{entity['entity_id']} {entity.get('friendly_name', '')}".lower()
    if _BLOCKLIST.search(text_all):
        return None
    if entity.get("device_class") in _BLOCK_DEVICE_CLASSES:
        return None
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
        if role is None or not is_bindable(e["entity_id"], role):
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

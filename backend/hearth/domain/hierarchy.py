"""HA hierarchy — integrations → devices → entities, with cascading relevance.

An entity's keep/skip is the most specific decision available: an explicit user/LLM
choice at entity → device → integration, else a heuristic. The heuristic is a cheap,
high-leverage funnel: a whole irrelevant integration (weather, backup) is skipped in
one call; infra devices (a Zigbee coordinator) are skipped; then each entity in a kept
device is judged individually (the actual sensor kept, its firmware-updater dropped).

Entities stay the ML feature unit — this only drives relevance / role hints / grouping.
Pure: data in, a verdict out. Decision storage is settings-backed (see load_decisions).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

KEEP, SKIP, UNSURE = "keep", "skip", "unsure"


class Integration(BaseModel):
    entry_id: str
    domain: str | None = None
    title: str | None = None
    state: str | None = None


class Device(BaseModel):
    id: str
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area: str | None = None
    via_device_id: str | None = None
    entry_type: str | None = None            # "service" = cloud/no hardware
    config_entries: list[str] = []


# Integration domains that never describe home activity → skip everything under them.
_INTEG_DENY = {"met", "metno", "openweathermap", "accuweather", "pirateweather",
               "forecast_solar", "sun", "moon", "backup", "hacs", "update",
               "system_health", "systemmonitor", "cloud", "google_translate", "tts",
               "radio_browser", "season", "workday", "uptime", "version",
               "homeassistant", "hassio", "supervisor", "waze_travel_time"}
# Integrations that expose real home sensors → keep wholesale (entities still role-checked).
_INTEG_ALLOW = {"zwave_js", "zha", "zigbee2mqtt", "matter", "mobile_app", "esphome",
                "hue", "deconz", "shelly", "tasmota", "sonoff", "tuya", "xiaomi_miio",
                "homekit_controller", "insteon", "fritzbox", "unifi"}
# Device names/models that are infrastructure, not a sensor of you.
_DEVICE_INFRA = re.compile(
    r"coordinator|bridge|\bhub\b|gateway|border\s*router|\brouter\b|dongle|conbee|"
    r"zbdongle|slzb|skyconnect|zbbridge|zwave.?stick")


def integration_relevance(domain: str | None) -> str:
    d = (domain or "").lower()
    if d in _INTEG_DENY:
        return SKIP
    if d in _INTEG_ALLOW:
        return KEEP
    return UNSURE


def device_relevance(device: Device | dict) -> str:
    d = device if isinstance(device, dict) else device.model_dump()
    if (d.get("entry_type") or "") == "service":
        return SKIP
    text = f"{d.get('name') or ''} {d.get('model') or ''} {d.get('manufacturer') or ''}".lower()
    if _DEVICE_INFRA.search(text):
        return SKIP
    return KEEP


def relevance_of(entity: dict, devices: dict, integrations: dict,
                 decisions: dict | None = None) -> tuple[str, str, str]:
    """(relevance, level, reason). `devices`/`integrations` keyed by id/entry_id;
    `decisions` = {'integration':{id:rel}, 'device':{id:rel}, 'entity':{eid:rel}}."""
    from .onboarding.advisor import is_noise, suggest_role
    decisions = decisions or {}
    eid = entity.get("entity_id", "")
    dev = devices.get(entity.get("device_id")) if entity.get("device_id") else None
    integ = integrations.get(entity.get("config_entry_id")) if entity.get("config_entry_id") else None

    # 1. explicit choice, most specific first
    if eid in (decisions.get("entity") or {}):
        return decisions["entity"][eid], "entity", "your choice"
    if dev and dev.get("id") in (decisions.get("device") or {}):
        return decisions["device"][dev["id"]], "device", "your choice"
    if integ and integ.get("entry_id") in (decisions.get("integration") or {}):
        return decisions["integration"][integ["entry_id"]], "integration", "your choice"

    # 2. heuristic — broad skips first (cheapest wins), then per-entity keep
    if integ and integration_relevance(integ.get("domain")) == SKIP:
        return SKIP, "integration", f"“{integ.get('title') or integ.get('domain')}” isn't about your home"
    if dev and device_relevance(dev) == SKIP:
        return SKIP, "device", "infrastructure / a hub, not a sensor of you"
    if is_noise(entity):
        return SKIP, "entity", "a diagnostic (battery, signal, firmware…)"
    role = suggest_role(entity)
    if role is not None:
        return KEEP, "entity", f"looks like {role.value}"
    if integ and integration_relevance(integ.get("domain")) == KEEP:
        return UNSURE, "device", "from a home integration, but no clear role yet"
    return UNSURE, "entity", "no clear signal — review"


# ── decision storage (settings-backed) ───────────────────────────────────────
_KEY = "ha.relevance"


def load_decisions(repo) -> dict:
    d = repo.get_setting(_KEY) or {}
    return {"integration": d.get("integration") or {}, "device": d.get("device") or {},
            "entity": d.get("entity") or {}}


def load_device_catalog(repo) -> dict:
    """Cached {device_id: {name, area, manufacturer, model}} from the last scan —
    so any surface can show device context (facts picker, coverage, drill-down)
    without another HA round-trip."""
    d = repo.get_setting("ha.devices")
    return d if isinstance(d, dict) else {}


def device_for_entity(repo, entity_id: str) -> dict | None:
    """The cached device an entity belongs to (or None) — via ha.entity_device."""
    did = (repo.get_setting("ha.entity_device") or {}).get(entity_id)
    return load_device_catalog(repo).get(did) if did else None


def device_label_for(repo, entity_id: str) -> str | None:
    """A short human label for an entity's device, e.g. 'Bed — Withings Sleep'."""
    d = device_for_entity(repo, entity_id)
    if not d:
        return None
    name, model = d.get("name"), d.get("model")
    if name and model and model.lower() not in name.lower():
        return f"{name} — {model}"
    return name or model or None


def set_decision(repo, level: str, node_id: str, relevance: str) -> None:
    if level not in ("integration", "device", "entity") or not node_id:
        return
    d = load_decisions(repo)
    d[level][node_id] = relevance
    repo.set_setting(_KEY, d)

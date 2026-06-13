"""Feature-name → plain English. Discovery signatures are raw feature columns
(`bed_sensor_bed_left_sensor_occupied`, `nieuwendijk_temperture_mean`); a human
can't name a pattern from those. This module reverses a column back to the
binding that produced it (`{binding.name}_{suffix}`, plus optional `_lag1`) and
renders a short, directional phrase ("Bed empty", "Bedroom warmer", "Alarm
soon"). Pure + dependency-free so it runs with or without an LLM key.

Columns that don't map to a binding (composite/spec features like
`alex_sleep_7d_avg_occupied`) fall back to a best-effort prettifier.
"""
from __future__ import annotations

import re

from ..schemas import Binding, Role

# (role, suffix) -> (phrase when ABOVE baseline, phrase when BELOW baseline).
# {place} = room or sensor name; {who} = person; {metric} = temperature/CO₂/…
_PHRASE: dict[tuple[Role, str], tuple[str, str]] = {
    (Role.PRESENCE, "frac"): ("active in {place}", "quiet in {place}"),
    (Role.PRESENCE, "any"): ("movement in {place}", "no movement in {place}"),
    (Role.PRESENCE, "transitions"): ("in and out of {place}", "settled in {place}"),
    (Role.BED, "occupied"): ("in bed", "bed empty"),
    (Role.BED, "mean"): ("in bed", "bed empty"),
    (Role.BED, "max"): ("in bed", "bed empty"),
    (Role.POWER, "on"): ("{place} in use", "{place} idle"),
    (Role.POWER, "max_w"): ("{place} drawing power", "{place} idle"),
    (Role.LIGHT, "on_frac"): ("{place} lights on", "{place} lights off"),
    (Role.LIGHT, "on_last"): ("{place} lights on", "{place} lights off"),
    (Role.MEDIA, "playing"): ("media playing in {place}", "media off in {place}"),
    (Role.MEDIA, "active"): ("media on in {place}", "media off in {place}"),
    (Role.MEDIA, "paused"): ("media paused in {place}", "media not paused"),
    (Role.ENV, "mean"): ("{place} {metric} higher", "{place} {metric} lower"),
    (Role.ENV, "max"): ("{place} {metric} higher", "{place} {metric} lower"),
    (Role.ENV, "delta"): ("{place} {metric} rising", "{place} {metric} falling"),
    (Role.PERSON, "home_frac"): ("{who} home", "{who} away"),
    (Role.PERSON, "home_last"): ("{who} home", "{who} away"),
    (Role.FOCUS, "on_last"): ("{who}'s phone in focus/Do-Not-Disturb", "{who}'s phone active"),
    (Role.ALARM_TIME, "minutes_until"): ("alarm a while off", "alarm coming up soon"),
    (Role.ALARM_TIME, "imminent"): ("alarm imminent", "no alarm imminent"),
    (Role.DOOR, "opened_any"): ("{place} door opened", "{place} door shut"),
    (Role.DOOR, "open_count"): ("{place} door busy", "{place} door quiet"),
    (Role.STEPS, "delta"): ("lots of steps ({who})", "few steps ({who})"),
    (Role.BATTERY, "delta"): ("{place} charging", "{place} battery steady"),
    (Role.CUSTOM, "mean"): ("{place} high", "{place} low"),
    (Role.CUSTOM, "max"): ("{place} high", "{place} low"),
    (Role.CUSTOM, "delta"): ("{place} rising", "{place} falling"),
}


def _metric_of(binding: Binding) -> str:
    """Guess what an env sensor measures, from its entity_id/name."""
    hay = f"{binding.entity_id} {binding.name}".lower()
    if "temp" in hay:
        return "temperature"
    if "humid" in hay or "vocht" in hay:
        return "humidity"
    if "co2" in hay or "co₂" in hay:
        return "CO₂"
    if "lux" in hay or "light" in hay or "illum" in hay:
        return "light level"
    return "level"


def prettify(raw: str) -> str:
    """Last-resort humaniser for columns with no binding (spec/composite
    features). `alex_sleep_7d_avg_occupied` -> 'Alex sleep 7-day avg occupied'."""
    s = raw[:-5] if raw.endswith("_lag1") else raw
    s = s.replace("_", " ")
    s = re.sub(r"\b7d\b", "7-day", s)
    s = re.sub(r"\bavg\b", "avg", s)
    s = re.sub(r"\bfrac\b", "fraction", s)
    s = s.strip()
    return s[:1].upper() + s[1:] if s else raw


def _match_binding(col: str, bindings: list[Binding]) -> tuple[Binding | None, str]:
    """Longest-prefix match of `col` against binding names (names can contain
    underscores). Returns (binding, suffix) or (None, "")."""
    base = col[:-5] if col.endswith("_lag1") else col
    best: Binding | None = None
    for b in bindings:
        if base == b.name or base.startswith(b.name + "_"):
            if best is None or len(b.name) > len(best.name):
                best = b
    if best is None:
        return None, ""
    suffix = base[len(best.name):].lstrip("_")
    return best, suffix


def humanize_feature(col: str, z: float, bindings: list[Binding],
                     persons: dict[str, str]) -> dict:
    """One signature feature -> {raw, label, room, role, dir}. `z`>0 means the
    feature sat ABOVE its usual level in this cluster (picks the up-phrase)."""
    up = z >= 0
    binding, suffix = _match_binding(col, bindings)
    lagged = col.endswith("_lag1")
    if binding is None:
        return {"raw": col, "label": prettify(col), "room": None,
                "role": None, "dir": "up" if up else "down"}

    place = binding.room or prettify(binding.name)
    who = persons.get(binding.person_id or "", "someone")
    tmpl = _PHRASE.get((binding.role, suffix))
    if tmpl is None:                                  # unknown suffix: generic
        verb = "higher" if up else "lower"
        label = f"{place} {prettify(suffix) or binding.role.value} {verb}"
    else:
        label = (tmpl[0] if up else tmpl[1]).format(
            place=place, who=who, metric=_metric_of(binding))
    if lagged:
        label += " (just before)"
    return {"raw": col, "label": label[:1].upper() + label[1:],
            "room": binding.room, "role": binding.role.value,
            "dir": "up" if up else "down"}

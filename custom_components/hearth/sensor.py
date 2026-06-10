"""One activity sensor per household member.

    sensor.hearth_<person>_activity
      state:       latest predicted activity slug ("sleeping", "cooking", …)
      attributes:  confidence, probabilities, smoothed, model, window time

Automate on it directly:  trigger: state, entity_id: sensor.hearth_alice_activity,
to: "sleeping"  →  lights off.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HearthActivitySensor(coordinator, person)
        for person in coordinator.persons
        if person.get("enabled", True)
    )


class HearthActivitySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:home-heart"

    def __init__(self, coordinator, person: dict) -> None:
        super().__init__(coordinator)
        self._pid = person["id"]
        self._attr_unique_id = f"hearth_{self._pid}_activity"
        self._attr_name = "Activity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._pid)},
            name=f"Hearth — {person.get('name') or self._pid}",
            manufacturer="Hearth",
            model="Activity model",
        )

    @property
    def _pred(self) -> dict | None:
        return (self.coordinator.data or {}).get(self._pid)

    @property
    def native_value(self) -> str | None:
        p = self._pred
        if p is None:
            return None
        return p.get("smoothed") or p.get("predicted")

    @property
    def extra_state_attributes(self) -> dict:
        p = self._pred or {}
        return {
            "confidence": p.get("confidence"),
            "evidence": p.get("evidence"),     # direct-signal share, 0–1
            "state_level": p.get("parent") or p.get("smoothed") or p.get("predicted"),
            # ^ coarse state (home/away/sleeping) — automate on this for
            #   stability; the main state may be a fine activity (eating)
            "probabilities": p.get("probs") or {},
            "raw_prediction": p.get("predicted"),
            "model": p.get("model_version") or p.get("model"),
            "window": p.get("time"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._pred is not None

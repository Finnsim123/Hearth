"""Manual activity override — one select per household member.

    select.hearth_<person>_override
      options:  "auto" (let the model decide) + your activity slugs
      set it:   pins the published activity and, while fresh, teaches the model
                (a confirmed label) — identical behaviour to the MQTT override.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

AUTO = "auto"


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HearthOverrideSelect(coordinator, person)
        for person in coordinator.persons
        if person.get("enabled", True)
    )


class HearthOverrideSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, coordinator, person: dict) -> None:
        super().__init__(coordinator)
        self._pid = person["id"]
        self._attr_unique_id = f"hearth_{self._pid}_override"
        self._attr_name = "Override activity"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._pid)})

    @property
    def options(self) -> list[str]:
        return [AUTO] + list(self.coordinator.activities or [])

    @property
    def current_option(self) -> str:
        ctrl = (self.coordinator.controls or {}).get(self._pid) or {}
        return ctrl.get("override", AUTO)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.client.set_override(self._pid, option)
        await self.coordinator.async_request_refresh()

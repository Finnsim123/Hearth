"""binary_sensor.hearth_attention — "Hearth needs you".

ON while there are active (non-dismissed) advisories: the model leaning on the
wrong sensors, a new device to integrate, a demoted foundational fact, coverage
gaps, an exhausted AI key. device_class: problem, so HA renders it as an issue.

The automation gem:  trigger: state, entity_id: binary_sensor.hearth_attention,
to: "on"  →  notify me — with the titles in the attributes.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HearthAttentionSensor(coordinator, entry)])


class HearthAttentionSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:bell-alert-outline"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "hearth_attention"
        self._attr_name = "Needs attention"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Hearth",
            manufacturer="Hearth",
            model="Activity brain",
            configuration_url=entry.data.get("host"),
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.advisories)

    @property
    def extra_state_attributes(self) -> dict:
        advs = self.coordinator.advisories or []
        return {
            "count": len(advs),
            "titles": [a.get("title") for a in advs],
            "worst_severity": max((a.get("severity") or "info" for a in advs),
                                  key=lambda s: {"info": 1, "warn": 2,
                                                 "critical": 3}.get(s, 0),
                                  default=None),
        }

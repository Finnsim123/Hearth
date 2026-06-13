"""Training-questions opt-out — one switch per household member.

    switch.hearth_<person>_questions
      on:   Hearth may ask this person training questions (default)
      off:  Hearth stops asking them — identical behaviour to the MQTT switch.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HearthQuestionsSwitch(coordinator, person)
        for person in coordinator.persons
        if person.get("enabled", True)
    )


class HearthQuestionsSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:comment-question"

    def __init__(self, coordinator, person: dict) -> None:
        super().__init__(coordinator)
        self._pid = person["id"]
        self._attr_unique_id = f"hearth_{self._pid}_questions"
        self._attr_name = "Activity questions"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._pid)})

    @property
    def is_on(self) -> bool:
        ctrl = (self.coordinator.controls or {}).get(self._pid) or {}
        return bool(ctrl.get("questions", True))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.client.set_questions(self._pid, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.client.set_questions(self._pid, False)
        await self.coordinator.async_request_refresh()

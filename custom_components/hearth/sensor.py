"""Per-member sensors.

    sensor.hearth_<person>_activity
      state:       latest predicted activity slug ("sleeping", "cooking", …)
      attributes:  confidence, probabilities, smoothed, model, window time

    sensor.hearth_<person>_accuracy   (Diagnostics section)
      state:       the model's honest headline accuracy, %
      attributes:  basis (real-world vs answers-so-far), validation status,
                   training phase, model version, train windows

Automate on them directly:  trigger: state, entity_id:
sensor.hearth_alice_activity, to: "sleeping"  →  lights off; or gate automations
on model health via the accuracy sensor.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for person in coordinator.persons:
        if not person.get("enabled", True):
            continue
        entities.append(HearthActivitySensor(coordinator, person))
        entities.append(HearthAccuracySensor(coordinator, person))
    async_add_entities(entities)


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


class HearthAccuracySensor(CoordinatorEntity, SensorEntity):
    """The model's honest headline accuracy, as a diagnostic on the person's
    device — real-world (random spot-checks) once ≥30 are gathered, else
    accuracy on answered questions, with the basis named in the attributes."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:target"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, person: dict) -> None:
        super().__init__(coordinator)
        self._pid = person["id"]
        self._attr_unique_id = f"hearth_{self._pid}_accuracy"
        self._attr_name = "Model accuracy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._pid)},
            name=f"Hearth — {person.get('name') or self._pid}",
            manufacturer="Hearth",
            model="Activity model",
        )

    @property
    def _diag(self) -> dict | None:
        return (self.coordinator.diagnostics or {}).get(self._pid)

    @property
    def native_value(self) -> float | None:
        d = self._diag
        return None if d is None else d.get("accuracy")

    @property
    def extra_state_attributes(self) -> dict:
        d = self._diag or {}
        return {
            "basis": d.get("basis"),
            "validation_status": d.get("validation_status"),
            "training_phase": d.get("phase"),
            "model_version": d.get("model_version"),
            "trained_at": d.get("trained_at"),
            "train_windows": d.get("train_windows"),
            "spot_checks": d.get("n_gold"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._diag is not None

from __future__ import annotations

from hearth.domain.onboarding.advisor import is_noise, suggest_role
from hearth.domain.schemas import Role


def _e(eid, **k):
    return {"entity_id": eid, "domain": eid.split(".")[0], **k}


def test_broadened_vocabulary_maps_more_real_sensors():
    assert suggest_role(_e("sensor.koffie_vermogen", unit="W")) == Role.POWER
    assert suggest_role(_e("sensor.meterkast_energie", unit="kWh")) == Role.POWER
    assert suggest_role(_e("binary_sensor.woonkamer_beweging", device_class="motion")) == Role.PRESENCE
    assert suggest_role(_e("sensor.air_pm1", device_class="pm1")) == Role.ENV
    assert suggest_role(_e("binary_sensor.voordeur", device_class="door")) == Role.DOOR
    assert suggest_role(_e("sensor.horloge_stappen", unit="steps")) == Role.STEPS


def test_diagnostics_are_noise_not_surfaced():
    assert is_noise(_e("sensor.phone_rssi", device_class="signal_strength"))
    assert is_noise(_e("button.reboot"))
    assert is_noise(_e("sensor.printer_firmware"))
    assert not is_noise(_e("binary_sensor.woonkamer_beweging", device_class="motion"))


def test_genuinely_unknown_stays_none():
    # no domain/dc/name/unit signal → unassigned (the long tail the UI surfaces)
    assert suggest_role(_e("sensor.woonkamer_node")) is None

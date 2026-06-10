from __future__ import annotations

from hearth.domain.onboarding.advisor import heuristic_bindings, is_bindable, suggest_role
from hearth.domain.schemas import Role


def _e(entity_id, **kw):
    return {"entity_id": entity_id, "domain": entity_id.split(".")[0],
            "friendly_name": kw.get("name"), "device_class": kw.get("dc"),
            "unit": kw.get("unit"), "area": kw.get("area"), "disabled": False}


def test_domain_rules():
    assert suggest_role(_e("light.kitchen")) is Role.LIGHT
    assert suggest_role(_e("media_player.tv")) is Role.MEDIA
    assert suggest_role(_e("person.somebody")) is Role.PERSON


def test_device_class_beats_name():
    assert suggest_role(_e("binary_sensor.weird_name_42", dc="occupancy")) is Role.PRESENCE
    assert suggest_role(_e("sensor.plug_3", dc="power", unit="W")) is Role.POWER


def test_name_patterns_generic():
    assert suggest_role(_e("sensor.bedroom_bed_voltage")) is Role.BED
    assert suggest_role(_e("sensor.waschmaschine_vermogen")) is Role.POWER
    assert suggest_role(_e("binary_sensor.phone_focus")) is Role.FOCUS


def test_unknown_left_unbound():
    assert suggest_role(_e("sensor.totally_mysterious")) is None
    out = heuristic_bindings([_e("sensor.totally_mysterious")])
    assert out == []


def test_unique_slugs():
    out = heuristic_bindings([_e("light.lamp"), _e("switch.lamp", dc="power")])
    names = [b.name for b in out]
    assert len(names) == len(set(names))


def test_diagnostics_blocklisted():
    for eid in ("sensor.wifi0_signal_quality", "sensor.home_assistant_core_cpu_percent",
                "sensor.temperatuur_5d", "sensor.regenkans_3d",
                "sensor.a1mini_print_progress", "sensor.openwrt_ping_drop_rate"):
        assert suggest_role(_e(eid)) is None, eid
    assert suggest_role(_e("sensor.plug_rssi", dc="signal_strength")) is None
    # real signals still bind
    assert suggest_role(_e("sensor.bedroom_temperature_temperatuur")) is not None


def test_role_domain_physics():
    from hearth.domain.schemas import Role
    assert not is_bindable("button.bed_sensor_calibrate_left", Role.BED)
    assert not is_bindable("scene.in_bed_both_home", Role.CUSTOM)
    assert not is_bindable("script.open_the_door", Role.DOOR)
    assert not is_bindable("number.bed_left_trigger_level", Role.BED)
    assert not is_bindable("update.alarmo_update", Role.ALARM_TIME)
    assert not is_bindable("sensor.har_prob_away", Role.ENV)          # old HAR outputs
    assert not is_bindable("sensor.strava_unknown_run_power", Role.POWER)
    assert not is_bindable("sensor.a1mini_print_bed_type", Role.BED)  # 3D printer!
    assert is_bindable("binary_sensor.bed_sensor_bed_left_occupancy", Role.BED)
    assert is_bindable("media_player.woonkamer", Role.MEDIA)
    assert is_bindable("lock.door", Role.DOOR)


def test_gate_is_appealable_but_physics_is_not():
    from hearth.domain.schemas import Role
    # input_boolean.in_bed helper: standard map now allows it for BED
    assert is_bindable("input_boolean.in_bed", Role.BED)
    # role-map mismatch: blocked by default, allowed with explicit override
    assert not is_bindable("switch.weird_bed_relay", Role.ENV)
    assert is_bindable("switch.weird_bed_relay", Role.ENV, override=True)
    # stateless domains: never bindable, override or not
    assert not is_bindable("scene.in_bed_both_home", Role.BED, override=True)
    assert not is_bindable("button.calibrate", Role.BED, override=True)
    # blocklist: never overridable either
    assert not is_bindable("sensor.har_prob_away", Role.ENV, override=True)

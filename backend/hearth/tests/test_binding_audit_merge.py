"""Binding audit fixes: per-device merge, brightness ≠ direct, no re-proposing
entities that already have a (possibly disabled) binding."""
from __future__ import annotations

from hearth.domain.binding_audit import _looks_direct, audit_bindings
from hearth.domain.schemas import Binding, Role


class FakeRepo:
    def __init__(self, bindings, entity_device):
        self._b = bindings
        self._ed = entity_device
    def bindings(self):
        return self._b
    def get_setting(self, key, default=None):
        if key == "ha.entity_device":
            return self._ed
        return default


def _b(i, eid, name, role=Role.ENV, enabled=True):
    b = Binding(entity_id=eid, role=role, name=name)
    b.id, b.enabled = i, enabled
    return b


def _setup(extra_entities=(), extra_bindings=()):
    bindings = [_b(1, "sensor.air_co2", "air_co2"),
                _b(2, "sensor.air_temperature", "air_temperature"),
                *extra_bindings]
    ed = {"sensor.air_co2": "dev1", "sensor.air_temperature": "dev1"}
    for e in extra_entities:
        ed[e] = "dev1"
    return FakeRepo(bindings, ed)


IMP = {"air_co2_mean": 0.03, "air_co2_delta": 0.01, "air_temperature_mean": 0.02}


def test_one_card_per_device_with_combined_reliance():
    repo = _setup(extra_entities=["switch.air_monitor_power"])
    fs = audit_bindings(repo, IMP)
    assert len(fs) == 1                              # was: one per feature family
    f = fs[0]
    assert f["kind"] == "bind_sibling"
    assert f["also_binding_ids"] == [2]
    assert abs(f["reliance"] - 1.0) < 1e-6           # all importance mass is here
    assert "air_co2, air_temperature" in f["why"]
    assert f["candidates"] == ["switch.air_monitor_power"]


def test_brightness_binaries_are_not_direct_signals():
    assert not _looks_direct("binary_sensor.nieuwendijk_brightness")
    assert not _looks_direct("sensor.room_illuminance")
    assert _looks_direct("switch.coffee_machine_power")
    # a device whose only sibling is a brightness binary -> no bind_sibling card
    repo = _setup(extra_entities=["binary_sensor.nieuwendijk_brightness"])
    assert all(f["kind"] != "bind_sibling" for f in audit_bindings(repo, IMP))


def test_disabled_binding_blocks_reproposal():
    # the sibling exists but a DISABLED binding already covers it (pruned as
    # empty) — proposing it again every audit round is thrash
    repo = _setup(extra_entities=["switch.air_monitor_power"],
                  extra_bindings=[_b(9, "switch.air_monitor_power",
                                     "air_monitor_power", role=Role.POWER,
                                     enabled=False)])
    assert all(f["kind"] != "bind_sibling" for f in audit_bindings(repo, IMP))

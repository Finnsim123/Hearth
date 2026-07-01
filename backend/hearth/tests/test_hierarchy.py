from __future__ import annotations

from hearth.domain.hierarchy import (
    device_relevance,
    integration_relevance,
    relevance_of,
)


def test_integration_heuristic():
    assert integration_relevance("met") == "skip"            # weather
    assert integration_relevance("mobile_app") == "keep"     # phone = steps/charging
    assert integration_relevance("zwave_js") == "keep"
    assert integration_relevance("some_niche") == "unsure"


def test_device_heuristic_infra_vs_real():
    assert device_relevance({"name": "Zigbee Coordinator"}) == "skip"
    assert device_relevance({"name": "SkyConnect", "model": "ZBT-1"}) == "skip"
    assert device_relevance({"entry_type": "service", "name": "OpenRouter"}) == "skip"
    assert device_relevance({"name": "Oral-B", "model": "IO Series 9"}) == "keep"


INTEGS = {"e_weather": {"entry_id": "e_weather", "domain": "met", "title": "Met.no"},
          "e_zigbee": {"entry_id": "e_zigbee", "domain": "zigbee2mqtt", "title": "Zigbee2MQTT"}}
DEVICES = {"d_coord": {"id": "d_coord", "name": "Zigbee Coordinator"},
           "d_plug": {"id": "d_plug", "name": "Kitchen Plug", "model": "Zigbee Plug"}}


def _e(eid, device_id=None, entry=None, **k):
    return {"entity_id": eid, "domain": eid.split(".")[0], "device_id": device_id,
            "config_entry_id": entry, "friendly_name": None, "device_class": None,
            "unit": None, **k}


def test_cascade_integration_skip_wins_cheaply():
    rel, level, _ = relevance_of(_e("sensor.rain", entry="e_weather"), DEVICES, INTEGS)
    assert rel == "skip" and level == "integration"


def test_cascade_infra_device_skipped():
    rel, level, _ = relevance_of(_e("sensor.coord_state", device_id="d_coord", entry="e_zigbee"),
                                 DEVICES, INTEGS)
    assert rel == "skip" and level == "device"


def test_cascade_keeps_real_entity_but_drops_its_diagnostic():
    keep, _, _ = relevance_of(_e("sensor.kitchen_plug_power", device_id="d_plug",
                                 entry="e_zigbee", device_class="power", unit="W"),
                              DEVICES, INTEGS)
    skip, lvl, _ = relevance_of(_e("sensor.kitchen_plug_rssi", device_id="d_plug",
                                   entry="e_zigbee", device_class="signal_strength"),
                                DEVICES, INTEGS)
    assert keep == "keep"
    assert skip == "skip" and lvl == "entity"       # the blend: same device, per-entity


def test_user_override_beats_heuristic():
    dec = {"integration": {"e_weather": "keep"}}
    rel, level, reason = relevance_of(_e("sensor.rain", entry="e_weather"),
                                      DEVICES, INTEGS, dec)
    assert rel == "keep" and level == "integration" and "choice" in reason

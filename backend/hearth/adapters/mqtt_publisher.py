"""EntityPublisher adapter — MQTT discovery (ADR-5).

Publishes retained discovery configs under homeassistant/… so HA creates a
'Hearth' device with per-person entities:

    sensor.hearth_<person>_activity      (smoothed; attrs: raw, confidence,
                                          probabilities, window_ts, because)
    sensor.hearth_<person>_confidence
    switch.hearth_<person>_questions     (asking-policy opt-out, two-way)
    select.hearth_<person>_override      (manual override, two-way)
    binary_sensor.hearth_alive           (availability topic)

Subscribes to HA's birth topic (homeassistant/status) and republishes discovery
on 'online'. Falls back to adapters/ha_rest state pushes when no broker is
configured (clearly marked degraded in the UI).
"""
from __future__ import annotations


class MqttPublisher:
    """Implements domain.ports.EntityPublisher."""

    def __init__(self, repo) -> None:  # AppRepo (broker config from UI)
        raise NotImplementedError

    def announce(self, persons, activities) -> None:
        raise NotImplementedError

    def publish(self, pred) -> None:
        raise NotImplementedError

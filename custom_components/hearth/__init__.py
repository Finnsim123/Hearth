"""Hearth — Home Assistant integration (HACS-distributed, ADR-11).

Connects to a local Hearth instance (host + API token), creates one device per
household member, AND handles the feedback loop end-to-end:

NO AUTOMATIONS, NO YAML. On setup this integration registers a listener on
HA's event bus for `mobile_app_notification_action` events whose action id
starts with "HEARTH_" and forwards them to the backend:

    async def _on_action(event):
        action = event.data.get("action", "")
        if action.startswith("HEARTH_"):
            await session.post(f"{host}/api/feedback/action",
                               json={"action": action,
                                     "device": event.data.get("device_name")},
                               headers={"Authorization": f"Bearer {token}"})

    entry.async_on_unload(
        hass.bus.async_listen("mobile_app_notification_action", _on_action))

So: install integration -> notifications tapped on any phone reach Hearth.
The legacy blueprint (deploy/ha/hearth_actions.yaml) exists only for setups
without this integration.

Phase 2 implements: WS client, entity platforms, the listener above.
"""
from __future__ import annotations

DOMAIN = "hearth"
PLATFORMS = ["sensor", "binary_sensor", "select", "switch"]

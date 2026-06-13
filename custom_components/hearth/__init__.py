"""Hearth — Home Assistant integration.

Connects to a local Hearth instance (host + API token) and closes the loop:

* one activity sensor per household member (predictions you can automate on)
* an event-bus listener that forwards notification action taps
  (`mobile_app_notification_action`, action id `HEARTH_<qid>_<idx>`) straight
  to the backend — NO automations, NO YAML.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HearthApiError, HearthAuthError, HearthClient
from .const import ACTION_PREFIX, CONF_HOST, CONF_TOKEN, DOMAIN, UPDATE_INTERVAL_S

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "select", "switch"]


class HearthCoordinator(DataUpdateCoordinator):
    """Polls persons, latest predictions and the two-way controls; the sensor,
    select and switch entities render from this."""

    def __init__(self, hass: HomeAssistant, client: HearthClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=UPDATE_INTERVAL_S))
        self.client = client
        self.persons: list[dict] = []
        self.controls: dict[str, dict] = {}   # {pid: {override, questions}}
        self.activities: list[str] = []        # override-select options

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            # refresh persons every poll: renames, enable/disable and newly
            # added members are reflected (new members need one HA reload to
            # create their entity — see async_setup_entry)
            self.persons = await self.client.persons()
            preds = await self.client.latest_predictions()
        except HearthAuthError as exc:
            raise ConfigEntryAuthFailed from exc
        except HearthApiError as exc:
            raise UpdateFailed(str(exc)) from exc
        try:                                   # controls are best-effort, not fatal
            c = await self.client.controls()
            self.controls = c.get("persons", {}) or {}
            self.activities = c.get("activities", []) or []
        except HearthApiError:
            _LOGGER.debug("controls fetch failed — keeping last known")
        return preds


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = HearthClient(entry.data[CONF_HOST], entry.data[CONF_TOKEN],
                          async_get_clientsession(hass))
    coordinator = HearthCoordinator(hass, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as exc:  # noqa: BLE001 — backend may still be booting
        raise ConfigEntryNotReady(str(exc)) from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── the feedback loop: notification taps → Hearth, no automations ──────
    async def _on_notification_action(event) -> None:
        action = str(event.data.get("action", ""))
        if not action.startswith(ACTION_PREFIX):
            return
        try:
            ok = await client.post_action(action, event.data.get("device_name"))
            _LOGGER.debug("forwarded %s to Hearth: %s", action, ok)
        except HearthApiError:
            _LOGGER.warning("could not forward %s to Hearth (backend down?)", action)

    entry.async_on_unload(
        hass.bus.async_listen("mobile_app_notification_action", _on_notification_action))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok

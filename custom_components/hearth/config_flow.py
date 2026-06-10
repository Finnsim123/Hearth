"""Config flow: host + API token, with zeroconf discovery pre-filling the host.

The token is minted in Hearth's onboarding wizard (step 9) or in
Settings → API tokens. One Hearth per HA instance.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HearthApiError, HearthAuthError, HearthClient
from .const import CONF_HOST, CONF_TOKEN, DOMAIN


class HearthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None

    async def _validate(self, host: str, token: str) -> str | None:
        """Returns an error key or None when the connection works."""
        client = HearthClient(host, token, async_get_clientsession(self.hass))
        try:
            await client.validate()
        except HearthAuthError:
            return "invalid_auth"
        except HearthApiError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].rstrip("/")
            if not host.startswith("http"):
                host = f"http://{host}"
            error = await self._validate(host, user_input[CONF_TOKEN])
            if error is None:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Hearth",
                    data={CONF_HOST: host, CONF_TOKEN: user_input[CONF_TOKEN]},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST,
                             default=self._discovered_host or "http://192.168.1.x:8420"): str,
                vol.Required(CONF_TOKEN): str,
            }),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info):
        """Hearth announces _hearth._tcp.local. — pre-fill the host."""
        self._discovered_host = f"http://{discovery_info.host}:{discovery_info.port}"
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Token rotated in Hearth — re-prompt for the token only."""
        self._discovered_host = entry_data.get(CONF_HOST)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = self._discovered_host or ""
            error = await self._validate(host, user_input[CONF_TOKEN])
            if error is None:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                self.hass.config_entries.async_update_entry(
                    entry, data={CONF_HOST: host, CONF_TOKEN: user_input[CONF_TOKEN]})
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

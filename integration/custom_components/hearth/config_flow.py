"""Config flow: host + API token, validated against GET /api/health.

Steps:
  zeroconf  -> Hearth announces _hearth._tcp.local. (backend
               adapters/zeroconf_announce.py); HA discovers it and starts this
               flow with HOST PRE-FILLED — the user only pastes the token.
               Dedup by the announced uuid so one Hearth = one entry.
  user      -> manual fallback: form (host, token) -> validate -> create entry
  reauth    -> token rotated in Hearth UI -> re-prompt token only

Either way the wizard's step 9 deep-links straight here via
{ha_url}/_my_redirect/config_flow_start?domain=hearth.

Errors surfaced: cannot_connect, invalid_auth, unsupported_version.
"""
from __future__ import annotations

# Phase 2: implement ConfigFlow (homeassistant.config_entries.ConfigFlow,
# domain=DOMAIN) with async_step_zeroconf / async_step_user / async_step_reauth.

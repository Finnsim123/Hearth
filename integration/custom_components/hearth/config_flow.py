"""Config flow: host + API token, validated against GET /api/health.

Steps:
  user      -> form (host, token) -> validate -> create entry
  reauth    -> token rotated in Hearth UI -> re-prompt token only

Errors surfaced: cannot_connect, invalid_auth, unsupported_version.
"""
from __future__ import annotations

# Phase 2: implement ConfigFlow (homeassistant.config_entries.ConfigFlow,
# domain=DOMAIN) with async_step_user / async_step_reauth.

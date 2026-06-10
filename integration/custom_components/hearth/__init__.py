"""Hearth — Home Assistant integration (HACS-distributed, ADR-11).

Connects to a local Hearth instance (host + API token from Hearth's Settings
page), subscribes to its WebSocket for push updates, and creates one device
per household member with activity/confidence sensors and two-way controls
(override select, questions switch).

Phase 2 of the roadmap implements this; the skeleton documents the shape:
  async_setup_entry: open WS client, fetch household, forward to platforms
  DataUpdateCoordinator-style push (no polling)
"""
from __future__ import annotations

DOMAIN = "hearth"
PLATFORMS = ["sensor", "binary_sensor", "select", "switch"]

"""Sensor platform: per-member activity + confidence.

sensor.hearth_<person>_activity   state = smoothed activity slug
  attributes: raw, confidence, probabilities (dict), window_ts,
              because (top SHAP contributions, human-readable)
sensor.hearth_<person>_confidence  unit '%'

Entities are registered under one device per member ("Hearth <name>") so
HA's UI groups them; updates arrive via the shared WS client (local push).
"""
from __future__ import annotations

# Phase 2: SensorEntity subclasses fed by the WS coordinator in __init__.py.

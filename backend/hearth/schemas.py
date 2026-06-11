"""Backwards-compatibility shim.

Schemas live in ``hearth.domain.schemas``. Models pickled by older builds embed
the module path ``hearth.schemas.*`` (e.g. ``hearth.schemas.Role``); without this
shim ``joblib.load`` raises ``ModuleNotFoundError: No module named 'hearth.schemas'``
and the fast-track / inference fall over. Re-exporting the same objects here lets
those pickles resolve to the canonical classes. New code must import from
``hearth.domain.schemas`` directly — this file exists only for old artifacts.
"""
from __future__ import annotations

from .domain.schemas import *  # noqa: F401,F403  (pickle attribute lookup)

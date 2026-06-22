"""Governor runtime — the one in-process state holder shared by the API and the
scheduler (one box, one process). Keeps api/ and scheduler/ from importing each
other: both depend on this domain module.

Unbound (tests, no psutil, no monitor) it stays NORMAL and admits everything, so
nothing changes behaviour until main.py binds a real monitor.
"""
from __future__ import annotations

import logging

from .governor import GovernorState, decide_state
from .vitals import GovernorConfig, Vitals

log = logging.getLogger(__name__)

_monitor = None            # ResourceMonitor
_config_fn = None          # callable -> GovernorConfig (e.g. from a repo setting)
_state: GovernorState = GovernorState.NORMAL
_last: Vitals | None = None
_pinned: GovernorState | None = None   # manual override (safe mode); None = auto


def bind(monitor, config_fn=None) -> None:
    global _monitor, _config_fn
    _monitor, _config_fn = monitor, config_fn


def config() -> GovernorConfig:
    if _config_fn is not None:
        try:
            return _config_fn()
        except Exception:
            pass
    return GovernorConfig()


def refresh() -> tuple[Vitals, GovernorState]:
    """Sample vitals and advance the governor state (hysteresis). Returns
    (vitals, state). A manual pin (safe mode) overrides automatic governance."""
    global _state, _last
    if _monitor is None:
        return (_last or Vitals()), (_pinned or _state)
    try:
        v = _monitor.sample()
    except Exception:
        log.exception("resource sample failed")
        return (_last or Vitals()), (_pinned or _state)
    _last = v
    if _pinned is not None:
        _state = _pinned
        return v, _state
    _state, _ = decide_state(_state, v, config())
    return v, _state


def state() -> GovernorState:
    return _pinned or _state


def last_vitals() -> Vitals | None:
    return _last


def set_mode(mode: str) -> GovernorState:
    """'safe' pins CRITICAL (inference-only); 'normal' resumes auto governance."""
    global _pinned, _state
    if mode == "safe":
        _pinned = GovernorState.CRITICAL
    elif mode == "normal":
        _pinned = None
    return state()

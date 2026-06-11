"""Feature recipe registry — one Recipe per Role (ADR-8).

Role metadata centralizes what the prototype had scattered as constants:
forward-fill limits, absence semantics (-1 'no sensor' vs 0 'no event'),
slow-sensor lookback. feature_set_version() hashes the active recipe set so
recipe changes bump the version and refuse mixed-version training.
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ..schemas import Binding, Role
from . import extractors as X

RecipeFn = Callable[[pd.Series, Binding], dict[str, float]]


@dataclass(frozen=True)
class Recipe:
    role: Role
    fn: RecipeFn
    ffill_limit_min: int          # role-aware forward-fill (1-min grid)
    absence_value: float          # NaN fill: 0 = "no event", -1 = "no sensor"
    slow_sensor: bool = False     # state-change-only writers (7-d lookback)
    suffixes: tuple[str, ...] = field(default_factory=tuple)  # for UI/versioning
    window_min: int = 30          # per-role lookback (shared window END, ADR-8).
                                  # Motion is informative over minutes; a step
                                  # counter only over hours. Default = base 30.


_REGISTRY: dict[Role, Recipe] = {}


def register(recipe: Recipe) -> None:
    _REGISTRY[recipe.role] = recipe


def recipe_for(role: Role) -> Recipe:
    return _REGISTRY[role]


def all_recipes() -> dict[Role, Recipe]:
    return dict(_REGISTRY)


PIPELINE_VERSION = "2"  # bump when extract_windows adds/changes columns


def feature_set_version(extra: list[dict] | None = None,
                        time_granularity: str = "coarse") -> str:
    """Deterministic hash of recipes (+ composites + time granularity).
    Changing the time encoding forces a clean retrain (old/new never mix)."""
    h = hashlib.sha256()
    h.update(PIPELINE_VERSION.encode())
    h.update(f"time:{time_granularity}".encode())
    for role in sorted(_REGISTRY, key=lambda r: r.value):
        r = _REGISTRY[role]
        h.update(role.value.encode())
        h.update(",".join(r.suffixes).encode())
        h.update(str((r.ffill_limit_min, r.absence_value, r.slow_sensor, r.window_min)).encode())
        h.update(inspect.getsource(r.fn).encode())
    for comp in (extra or []):
        h.update(str(sorted(comp.items())).encode())
    return "v" + h.hexdigest()[:10]


# ── default recipe set (ffill limits ported from the prototype) ────────────
# window_min = per-role lookback ending at the shared 30-min window edge.
# Fast/event-like roles look back minutes (responsive); slow accumulators look
# back hours (a 3-h step delta is meaningful, a 15-min one is noise).
register(Recipe(Role.PRESENCE,  X.presence,   5,    0.0, suffixes=("frac", "any", "transitions"), window_min=15))
register(Recipe(Role.BED,       X.bed,        10,  -1.0, suffixes=("mean", "max", "occupied"), window_min=30))
register(Recipe(Role.POWER,     X.power,      10,   0.0, suffixes=("on", "max_w"), window_min=30))
register(Recipe(Role.LIGHT,     X.light,      240,  0.0, suffixes=("on_last", "on_frac"), window_min=30))
register(Recipe(Role.MEDIA,     X.media,      5,    0.0, suffixes=("playing", "paused", "active"), window_min=15))
register(Recipe(Role.ENV,       X.env,        120,  0.0, suffixes=("mean", "delta", "max"), window_min=60))
register(Recipe(Role.PERSON,    X.person,     10080, -1.0, slow_sensor=True, suffixes=("home_last", "home_frac"), window_min=30))
register(Recipe(Role.FOCUS,     X.focus,      10,   0.0, suffixes=("on_last",), window_min=30))
register(Recipe(Role.ALARM_TIME, X.alarm_time, 10080, 0.0, slow_sensor=True, suffixes=("minutes_until", "imminent"), window_min=30))
register(Recipe(Role.DOOR,      X.door,       0,    0.0, suffixes=("opened_any", "open_count"), window_min=30))
register(Recipe(Role.STEPS,     X.steps,      30,   0.0, suffixes=("delta",), window_min=180))
register(Recipe(Role.BATTERY,   X.battery,    120,  0.0, suffixes=("delta",), window_min=180))
register(Recipe(Role.CUSTOM,    X.custom,     30,   0.0, suffixes=("mean", "max", "delta"), window_min=30))

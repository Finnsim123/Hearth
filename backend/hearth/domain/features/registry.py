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


_REGISTRY: dict[Role, Recipe] = {}


def register(recipe: Recipe) -> None:
    _REGISTRY[recipe.role] = recipe


def recipe_for(role: Role) -> Recipe:
    return _REGISTRY[role]


def all_recipes() -> dict[Role, Recipe]:
    return dict(_REGISTRY)


def feature_set_version(extra: list[dict] | None = None) -> str:
    """Deterministic hash of recipes (+ composite definitions). 'v' + 10 hex."""
    h = hashlib.sha256()
    for role in sorted(_REGISTRY, key=lambda r: r.value):
        r = _REGISTRY[role]
        h.update(role.value.encode())
        h.update(",".join(r.suffixes).encode())
        h.update(str((r.ffill_limit_min, r.absence_value, r.slow_sensor)).encode())
        h.update(inspect.getsource(r.fn).encode())
    for comp in (extra or []):
        h.update(str(sorted(comp.items())).encode())
    return "v" + h.hexdigest()[:10]


# ── default recipe set (ffill limits ported from the prototype) ────────────
register(Recipe(Role.PRESENCE,  X.presence,   5,    0.0, suffixes=("frac", "any", "transitions")))
register(Recipe(Role.BED,       X.bed,        10,  -1.0, suffixes=("mean", "max", "occupied")))
register(Recipe(Role.POWER,     X.power,      10,   0.0, suffixes=("on", "max_w")))
register(Recipe(Role.LIGHT,     X.light,      240,  0.0, suffixes=("on_last", "on_frac")))
register(Recipe(Role.MEDIA,     X.media,      5,    0.0, suffixes=("playing", "paused", "active")))
register(Recipe(Role.ENV,       X.env,        120,  0.0, suffixes=("mean", "delta", "max")))
register(Recipe(Role.PERSON,    X.person,     10080, -1.0, slow_sensor=True, suffixes=("home_last", "home_frac")))
register(Recipe(Role.FOCUS,     X.focus,      10,   0.0, suffixes=("on_last",)))
register(Recipe(Role.ALARM_TIME, X.alarm_time, 10080, 0.0, slow_sensor=True, suffixes=("minutes_until", "imminent")))
register(Recipe(Role.DOOR,      X.door,       0,    0.0, suffixes=("opened_any", "open_count")))
register(Recipe(Role.STEPS,     X.steps,      30,   0.0, suffixes=("delta",)))
register(Recipe(Role.BATTERY,   X.battery,    120,  0.0, suffixes=("delta",)))
register(Recipe(Role.CUSTOM,    X.custom,     30,   0.0, suffixes=("mean", "max", "delta")))

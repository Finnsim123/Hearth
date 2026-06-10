"""Feature recipe registry — one recipe per Role (ADR-8).

A recipe turns one binding's 1-min series into named feature columns for a
window. Registration is declarative so the UI can show users exactly which
features each sensor contributes, and so feature_set versioning can hash the
active recipe set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ..schemas import Binding, Role

# (series_1min_within_window, binding) -> {feature_suffix: value}
RecipeFn = Callable[[pd.Series, Binding], dict[str, float]]


@dataclass
class Recipe:
    role: Role
    fn: RecipeFn
    ffill_limit_min: int  # forward-fill semantics (role metadata, not magic numbers)
    absence_value: float  # what a missing sensor means: 0 = "no event", -1 = "no sensor"
    slow_sensor: bool = False  # state-change-only writers need 7-day lookback
    feature_suffixes: list[str] = field(default_factory=list)  # for UI display


_REGISTRY: dict[Role, Recipe] = {}


def register(recipe: Recipe) -> None:
    _REGISTRY[recipe.role] = recipe


def recipe_for(role: Role) -> Recipe:
    return _REGISTRY[role]


def feature_set_version() -> str:
    """Deterministic hash of the active recipes; bumps trigger backfill.
    Phase 1: hash role names + suffixes + recipe source."""
    raise NotImplementedError

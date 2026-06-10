"""Rule engine — minimal weak supervision (Snorkel-pattern, no dependency).

Each Rule is a JSON predicate over feature columns mapped to an activity.
Rules are user-edited in the UI or drafted from named clusters. Evaluation is
vectorized over a feature matrix; priority resolves conflicts (lower wins).
Outputs provenance=BOOTSTRAP labels.
"""
from __future__ import annotations

import pandas as pd

from ..schemas import Rule


def evaluate_predicate(predicate: dict, features: pd.DataFrame) -> pd.Series:
    """JSON AST -> boolean mask. Supported: {all|any: [...]}, leaf
    {feat, op(>,<,>=,<=,==,!=), value}. No eval(), no code execution."""
    raise NotImplementedError


def bootstrap_labels(rules: list[Rule], features: pd.DataFrame, person_id: str) -> pd.Series:
    """Apply person's rules by priority; unmatched windows get the household's
    default activity (configurable, e.g. 'home'). Returns activity-slug series."""
    raise NotImplementedError


def draft_rule_from_signature(signature: list[tuple[str, float]], activity_slug: str) -> Rule:
    """Turn a cluster signature into a proposed Rule (origin='discovered')
    for the user to accept or edit. Thresholds from signature z-scores."""
    raise NotImplementedError

"""Rule engine — minimal weak supervision (Snorkel-pattern, no dependency).

Predicates share the composite AST grammar (features/composites.py) so users
learn ONE predicate language for both rules and composite features.
bootstrap_labels lands fully in Phase 2; the evaluator is live now.
"""
from __future__ import annotations

import pandas as pd

from ..features.composites import evaluate_ast
from ..schemas import Rule


def evaluate_predicate(predicate: dict, features: pd.DataFrame) -> pd.Series:
    """JSON AST -> boolean mask. No eval(), no code execution."""
    return evaluate_ast(predicate, features)


def bootstrap_labels(rules: list[Rule], features: pd.DataFrame,
                     person_id: str, default_activity: str = "home") -> pd.Series:
    """Apply person-applicable rules by priority (lower wins on conflict)."""
    labels = pd.Series(default_activity, index=features.index, dtype=object)
    decided = pd.Series(False, index=features.index)
    applicable = [r for r in rules if r.enabled and r.person_id in (None, person_id)]
    for rule in sorted(applicable, key=lambda r: r.priority):
        mask = evaluate_predicate(rule.predicate, features) & ~decided
        labels[mask] = rule.activity_slug
        decided |= mask
    return labels


def draft_rule_from_signature(signature: list[tuple[str, float]], activity_slug: str) -> Rule:
    """Cluster signature -> proposed Rule (origin='discovered') for approval.
    Positive-z features become '> 0.5'-style conditions (Phase 4 refines)."""
    conditions = [{"feat": feat, "op": ">", "value": 0.5}
                  for feat, z in signature[:3] if z > 0]
    return Rule(activity_slug=activity_slug, predicate={"all": conditions},
                priority=50, origin="discovered", enabled=False)

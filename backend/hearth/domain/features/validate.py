"""Feature spec validation — the gate every feature passes before execution.

Extends the rule-AST whitelist discipline (openrouter_llm.validate_predicate) to
feature definitions (llm_layer_design §d). A feature is accepted only if, in
order: it has a legal name, its transform is in the ACTIVE whitelist, its params
match that transform's schema, its window is in range, its info tier is
compatible with the transform, its inputs exist and are of the right kind, it
does not rest solely on an unusable sensor, and the spec is under budget.

Pure functions, no I/O, no LLM, no eval. Anything failing is dropped with a
reason (for logging / the UI), never raised — a bad spec degrades, never crashes.
"""
from __future__ import annotations

import re

from ..schemas import FeatureDef, FeatureSpec
from . import transforms as T

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
WINDOW_MIN_LO, WINDOW_MIN_HI = 1, 1440
MAX_FEATURES_DEFAULT = 250


def validate_feature(
    feature: FeatureDef, *, whitelist: dict[str, T.TransformSpec],
    kept_entities: set[str], reliability: dict[str, str],
    tiers: dict[str, object], defined_names: set[str],
    base_features: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """Validate ONE feature against the active whitelist and the spec's
    selections. Returns (ok, reason). `tiers` maps entity_id -> InfoTier (from
    the selections); `defined_names` are feature names already accepted earlier
    in the spec (composites may reference those, or `base_features`)."""
    if not isinstance(feature, FeatureDef):
        return False, "not a FeatureDef"
    if not NAME_RE.match(feature.name or ""):
        return False, "name must match ^[a-z][a-z0-9_]{0,59}$"
    if feature.name in defined_names:
        return False, "duplicate feature name"
    spec = whitelist.get(feature.transform)
    if spec is None:
        return False, "transform not in active whitelist"
    if not T.check_params(spec, feature.params):
        return False, "params do not match transform schema"
    if feature.window_min is not None and not (WINDOW_MIN_LO <= feature.window_min <= WINDOW_MIN_HI):
        return False, "window_min out of range"
    if not feature.inputs:
        return False, "feature has no inputs"

    if spec.input_kind == T.ENTITY:
        for e in feature.inputs:
            if e not in kept_entities:
                return False, f"input is not a kept entity: {e}"
        # tier compatibility — use the entity's assigned tier, else the feature's.
        # (this is what stops e.g. window_mean on a T4 cumulative counter.)
        for e in feature.inputs:
            tier = tiers.get(e) or feature.info_tier
            if tier is None or tier.value not in spec.tiers:
                return False, "info tier incompatible with transform"
        rels = [reliability.get(e, "ok") for e in feature.inputs]
        if rels and all(r == "unusable" for r in rels):
            return False, "all inputs flagged unusable"
    else:  # FEATURE inputs (composite)
        for fn in feature.inputs:
            if fn not in defined_names and fn not in base_features:
                return False, f"composite input not defined earlier: {fn}"
    return True, "ok"


def validate_spec(
    spec: FeatureSpec, *, mode: str = T.DEFAULT_MODE,
    base_features: frozenset[str] = frozenset(),
    max_features: int = MAX_FEATURES_DEFAULT,
) -> tuple[FeatureSpec, list[tuple[str, str]]]:
    """Filter a spec down to its valid, executable features. Processes features
    in order so a composite may reference earlier ones; enforces the budget cap.
    Returns (clean_spec, rejections) where rejections is [(name, reason)].
    Selections are passed through unchanged."""
    wl = T.whitelist(mode)
    kept = {s.entity_id for s in spec.selections if s.keep}
    reliability = {s.entity_id: s.reliability for s in spec.selections}
    tiers = {s.entity_id: s.info_tier for s in spec.selections if s.info_tier is not None}

    defined: set[str] = set()
    accepted: list[FeatureDef] = []
    rejected: list[tuple[str, str]] = []
    for f in spec.features:
        if len(accepted) >= max_features:
            rejected.append((f.name, "over feature budget"))
            continue
        ok, reason = validate_feature(
            f, whitelist=wl, kept_entities=kept, reliability=reliability,
            tiers=tiers, defined_names=defined, base_features=base_features)
        if ok:
            accepted.append(f)
            defined.add(f.name)
        else:
            rejected.append((f.name, reason))
    return spec.model_copy(update={"features": accepted}), rejected

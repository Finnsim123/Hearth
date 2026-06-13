"""Transform whitelist — the executable vocabulary of the feature spec.

The safety boundary (CAAFE pattern, llm_layer_design §d): the LLM may only
reference transform ids that exist HERE, and the builder (later commit) only
knows how to execute these. Anything else is rejected before execution. No LLM
ever writes code; it selects and parameterizes vetted operations.

This module is METADATA ONLY: each TransformSpec declares the info tiers it is
valid for, whether it takes entity ids or existing feature names as inputs, and
its parameter schema. Executor functions and builder wiring land in a later
commit; the validator (validate_feature) consumes this metadata.

Two whitelist MODES (llm_layer_design intro; gap analysis C7):
  conservative — only transforms that reproduce today's role-recipe outputs plus
                 the basic composites. The safe default; smallest surface.
  full         — adds the richer parameterized transforms (slope, baseline
                 deviation, sequences, room transitions, …).
Same mechanism, different whitelist contents — switchable as a setting.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas import InfoTier

# input kinds
ENTITY = "entity"     # inputs are HA entity ids (per-entity transforms)
FEATURE = "feature"   # inputs are existing feature names (composites)

# pseudo-tier marking a cross-entity composite (it has no single info tier)
COMPOSITE = "composite"

# parameter type tokens understood by check_params()
_TYPE_TOKENS = ("int", "float", "str", "list[str]")

WHITELIST_MODES = ("conservative", "full")
DEFAULT_MODE = "conservative"


@dataclass(frozen=True)
class TransformSpec:
    id: str
    tiers: frozenset[str]      # InfoTier .value codes it applies to, or {COMPOSITE}
    input_kind: str            # ENTITY or FEATURE
    params: dict[str, str]     # name -> type token; empty = takes no params
    conservative: bool         # True = present in the conservative whitelist too
    summary: str = ""


def _t(*tiers: InfoTier) -> frozenset[str]:
    return frozenset(t.value for t in tiers)


# ── the registry ────────────────────────────────────────────────────────────
# tiers use InfoTier codes (T1..T5); composites use {COMPOSITE}.
_SPECS: list[TransformSpec] = [
    # T1 discrete event gate
    TransformSpec("occupancy_fraction", _t(InfoTier.DISCRETE_EVENT_GATE), ENTITY, {}, True,
                  "fraction of the window the gate was active (≈ presence _frac)"),
    TransformSpec("any_active", _t(InfoTier.DISCRETE_EVENT_GATE), ENTITY, {}, True,
                  "1 if the gate was ever active in the window (≈ _any)"),
    TransformSpec("transition_count",
                  _t(InfoTier.DISCRETE_EVENT_GATE, InfoTier.STATE_MACHINE), ENTITY, {}, True,
                  "number of state changes in the window (≈ _transitions)"),
    TransformSpec("run_length_on", _t(InfoTier.DISCRETE_EVENT_GATE), ENTITY, {}, False,
                  "longest continuous on-run within the window"),
    TransformSpec("time_since_last_change",
                  _t(InfoTier.DISCRETE_EVENT_GATE, InfoTier.STATE_MACHINE, InfoTier.SLOW_STATE),
                  ENTITY, {"cap_min": "int"}, False,
                  "minutes since the last change at window end, capped"),
    # T2 state machine
    TransformSpec("state_onehot", _t(InfoTier.STATE_MACHINE), ENTITY, {"states": "list[str]"}, False,
                  "one-hot of the state at window end over the given states"),
    TransformSpec("state_dwell_fraction", _t(InfoTier.STATE_MACHINE), ENTITY, {"state": "str"}, False,
                  "fraction of the window spent in a given state"),
    TransformSpec("last_state", _t(InfoTier.STATE_MACHINE, InfoTier.SLOW_STATE), ENTITY, {}, True,
                  "the state at window end (≈ _on_last / _home_last)"),
    # T3 continuous measurement
    TransformSpec("window_mean", _t(InfoTier.CONTINUOUS_MEASUREMENT), ENTITY, {}, True,
                  "mean of the values in the window (≈ env _mean)"),
    TransformSpec("window_max", _t(InfoTier.CONTINUOUS_MEASUREMENT), ENTITY, {}, True,
                  "max value in the window (≈ _max)"),
    TransformSpec("window_minimum", _t(InfoTier.CONTINUOUS_MEASUREMENT), ENTITY, {}, False,
                  "min value in the window"),
    TransformSpec("window_delta",
                  _t(InfoTier.CONTINUOUS_MEASUREMENT, InfoTier.CUMULATIVE_COUNTER), ENTITY, {}, True,
                  "last minus first value across the window (≈ env _delta)"),
    TransformSpec("window_slope", _t(InfoTier.CONTINUOUS_MEASUREMENT), ENTITY, {}, False,
                  "least-squares slope of the values over the window"),
    TransformSpec("deviation_from_daily_baseline", _t(InfoTier.CONTINUOUS_MEASUREMENT), ENTITY,
                  {"baseline_days": "int"}, False,
                  "current value minus its same-time-of-day baseline"),
    # T4 cumulative counter (rate only — never the raw value)
    TransformSpec("counter_rate", _t(InfoTier.CUMULATIVE_COUNTER), ENTITY, {}, False,
                  "increase per minute across the window (kWh, steps)"),
    # T5 slow state
    TransformSpec("home_fraction", _t(InfoTier.SLOW_STATE), ENTITY, {}, True,
                  "fraction of a long lookback the state read home (≈ person _home_frac)"),
    # composites (inputs are existing feature names)
    TransformSpec("co_occurrence_and", frozenset({COMPOSITE}), FEATURE, {"threshold": "float"}, True,
                  "1 when all input features exceed the threshold together"),
    TransformSpec("co_occurrence_count", frozenset({COMPOSITE}), FEATURE, {}, False,
                  "how many input features are active together"),
    TransformSpec("sequence_within", frozenset({COMPOSITE}), FEATURE, {"max_gap_min": "int"}, False,
                  "1 when input features fire in order within a max gap"),
    TransformSpec("room_transition_count", frozenset({COMPOSITE}), FEATURE, {"rooms": "list[str]"}, False,
                  "count of presence transitions between the given rooms"),
    TransformSpec("absence_and", frozenset({COMPOSITE}), FEATURE, {}, True,
                  "1 when all input gates are simultaneously OFF (absence context)"),
]

_REGISTRY: dict[str, TransformSpec] = {s.id: s for s in _SPECS}


def all_transforms() -> dict[str, TransformSpec]:
    return dict(_REGISTRY)


def get_transform(transform_id: str) -> TransformSpec | None:
    return _REGISTRY.get(transform_id)


def whitelist(mode: str = DEFAULT_MODE) -> dict[str, TransformSpec]:
    """The active transform set for a mode. Unknown mode -> conservative."""
    if mode == "full":
        return all_transforms()
    return {tid: s for tid, s in _REGISTRY.items() if s.conservative}


def whitelist_ids(mode: str = DEFAULT_MODE) -> set[str]:
    return set(whitelist(mode))


def active_mode(repo) -> str:
    """Feature power mode from the 'feature.power_mode' setting (gap analysis
    C7; the setting itself is wired in a later commit). Defaults to conservative."""
    try:
        m = repo.get_setting("feature.power_mode", DEFAULT_MODE)
    except Exception:
        m = DEFAULT_MODE
    return m if m in WHITELIST_MODES else DEFAULT_MODE


def _type_ok(value, token: str) -> bool:
    if token == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if token == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if token == "str":
        return isinstance(value, str)
    if token == "list[str]":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    return False


def check_params(spec: TransformSpec, params: dict | None) -> bool:
    """True iff `params` exactly matches the transform's schema: every declared
    param present and correctly typed, and no unknown params. A transform with
    an empty schema requires an empty params dict."""
    params = params or {}
    if not isinstance(params, dict):
        return False
    for name, token in spec.params.items():
        if name not in params or not _type_ok(params[name], token):
            return False
    return all(name in spec.params for name in params)

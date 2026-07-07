"""Evidence tiers — how much should we trust the signals behind a prediction?

Research grounding (docs/RESEARCH.md §Evidence):
  * HAR surveys split sensors into direct event sensors vs ambient ones;
    ambient (temp/CO2/humidity) is environment-sensitive and only weakly
    activity-specific — context, not evidence. CASAS, the canonical smart-home
    corpus, is built almost entirely on binary event sensors for this reason.
  * Home Assistant's Bayesian sensor expresses the same idea numerically:
    every observation carries prob_given_true/false — strong evidence has a
    high likelihood ratio, ambient evidence sits near 1.
  * The spurious-correlation literature warns models latch onto proxies that
    happen to co-vary with the target in one period (see: the partner-alarm
    incident). The cure is grouping features by trustworthiness and AUDITING
    reliance — not blanket exclusion: ambient features genuinely help
    (CO2 delta for cooking), so we measure and expose, never silently drop.

Tiers (data, not code — per-binding override via options["tier"], so the LLM
or the user can re-categorize an unusual sensor):
  1 DIRECT      bed, room presence, person tracker, media player, door,
                own-phone focus & alarm — fires because a HUMAN did something
  2 BEHAVIORAL  appliance power, lights, steps — usually human-caused,
                but automations/schedules also move them
  3 AMBIENT     temperature, CO2, humidity, battery — drifts, lags, and
                correlates with everything
  0 PRIOR       time-of-day & composites — useful, but "it's usually X at
                23:00" is a prior, not evidence about THIS window

Two consumers:
  * trainer  → evidence_profile(importances): where the model's weight sits
  * inference → window_evidence(shap_row): was THIS prediction anchored on
    direct signal? Low direct share caps confidence below the ask threshold,
    so weakly-evidenced predictions ask instead of assert.
"""
from __future__ import annotations

from ..schemas import Binding, Role

ROLE_TIER: dict[Role, int] = {
    Role.BED: 1, Role.PRESENCE: 1, Role.PERSON: 1, Role.MEDIA: 1,
    Role.DOOR: 1, Role.FOCUS: 1, Role.ALARM_TIME: 1,
    Role.POWER: 2, Role.LIGHT: 2, Role.STEPS: 2, Role.CUSTOM: 2,
    Role.ENV: 3, Role.BATTERY: 3,
}

TIER_NAMES = {1: "direct", 2: "behavioral", 3: "ambient", 0: "prior"}

# Below this direct-tier SHAP share, a prediction is "weakly evidenced":
# confidence is capped under the ask threshold so Hearth asks, not asserts.
WEAK_DIRECT_SHARE = 0.25
WEAK_CONFIDENCE_CAP = 0.70  # ASK_THRESHOLD is 0.75


def binding_tiers(bindings: list[Binding]) -> dict[str, int]:
    """{binding_name(=feature prefix): tier}. options['tier'] overrides the
    role default — the appealable-gate principle applied to trust."""
    out: dict[str, int] = {}
    for b in bindings:
        tier = b.options.get("tier")
        try:
            tier = int(tier) if tier is not None else ROLE_TIER.get(b.role, 2)
        except (TypeError, ValueError):
            tier = ROLE_TIER.get(b.role, 2)
        out[b.name] = max(1, min(3, tier))
    return out


def tier_of_column(col: str, prefixes: dict[str, int]) -> int:
    """Feature column → tier; longest matching binding prefix wins;
    unmatched columns (hour_of_day, composites) are tier 0 = prior."""
    # home-mobility / anchor-distance are derived from presence movement across
    # rooms — behavioural (tier 2), not a time-of-day prior.
    if col.startswith("mob_") or col.startswith("dist_to_"):
        return 2
    best, best_len = 0, -1
    for name, tier in prefixes.items():
        if (col == name or col.startswith(name + "_")) and len(name) > best_len:
            best, best_len = tier, len(name)
    return best


def evidence_profile(weights: dict[str, float],
                     bindings: list[Binding]) -> dict[str, float]:
    """Share of (importance/SHAP) mass per tier, normalized to sum 1.
    weights: {feature_column: weight} — sign is ignored, magnitude counts."""
    prefixes = binding_tiers(bindings)
    mass = {name: 0.0 for name in TIER_NAMES.values()}
    for col, w in weights.items():
        mass[TIER_NAMES[tier_of_column(col, prefixes)]] += abs(float(w))
    total = sum(mass.values())
    if total <= 0:
        return {}
    return {k: round(v / total, 4) for k, v in mass.items()}


def window_evidence(shap_row, bindings: list[Binding]) -> float:
    """Direct-tier share of |SHAP| mass for one prediction window ∈ [0, 1]."""
    profile = evidence_profile(
        {c: float(v) for c, v in shap_row.items()}, bindings)
    return profile.get("direct", 0.0)


def evidence_label(direct_share: float) -> str:
    if direct_share >= 0.5:
        return "strong"
    if direct_share >= WEAK_DIRECT_SHARE:
        return "mixed"
    return "weak"

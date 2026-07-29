"""Output post-processing policy — the abstain / "unknown" state.

When the model is not confident enough to commit, it is better for automations
to do nothing than to act on a wrong guess (a wrong 'movie' dims the lights
mid-dinner; 'unknown' does nothing). So below an abstain threshold the PUBLISHED
(smoothed) state becomes "unknown" — exposed to Home Assistant as a first-class
state — while the raw prediction, probabilities and confidence are preserved for
transparency (model_levers.md G6; gap analysis G5).
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace

UNKNOWN = "unknown"


@dataclass(frozen=True)
class OutputPolicy:
    """Prediction post-processing knobs as DATA (the 'output.policy' setting).
    abstain_threshold is read on the (calibrated, evidence-capped) confidence."""
    abstain_enabled: bool = True
    abstain_threshold: float = 0.4


def load_output_policy(repo) -> OutputPolicy:
    """OutputPolicy with overrides from the 'output.policy' setting merged over
    the defaults; bad values degrade to defaults and never break inference."""
    try:
        raw = repo.get_setting("output.policy") or {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict) or not raw:
        return OutputPolicy()
    names = {f.name for f in fields(OutputPolicy)}
    clean = {}
    for k, v in raw.items():
        if k not in names:
            continue
        if k == "abstain_enabled" and isinstance(v, bool):
            clean[k] = v
        elif k == "abstain_threshold" and isinstance(v, (int, float)) and not isinstance(v, bool):
            clean[k] = float(v)
    try:
        return replace(OutputPolicy(), **clean)
    except Exception:
        return OutputPolicy()


def apply_abstain(state: str, confidence: float, pol: OutputPolicy,
                  pred_set: list | None = None) -> str:
    """Return UNKNOWN when abstaining is enabled and either (a) the conformal
    prediction set is EMPTY — at the calibrated level this window doesn't look
    like ANY known activity, the principled novelty signal — or (b) confidence
    is below the plain threshold. pred_set=None (no calibration yet, rules
    fallback, realtime path) keeps the original threshold-only behaviour.
    Applied to the PUBLISHED state; raw prediction stays visible either way."""
    if not pol.abstain_enabled:
        return state
    if pred_set is not None and len(pred_set) == 0:
        return UNKNOWN
    if confidence < pol.abstain_threshold:
        return UNKNOWN
    return state

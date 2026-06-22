"""Foundational resolver — the fact-first precedence cascade (foundational_facts_
design §5). One window in, one resolved activity out, with a `basis` so the UI/HA
know whether it was KNOWN or INFERRED.

Cascade (highest wins, each short-circuits the rest):
  1. manual override        — the user said so
  2. gating facts           — away / empty / asleep (earned 'fact' status; §7a)
  3. masked ML              — model argmax, with constraining facts zeroing
                              impossible classes, subject to the abstain threshold
  4. rule fallback          — deterministic rule when the model abstains (cold start)
  5. unknown                — abstain

`needs_model(ctx)` lets the caller SKIP feature-build + inference entirely when an
override or a gating fact already settles the window (correctness + compute saving —
this is where the governor and the cascade meet). Pure: the predictor assembles the
context from override / earned facts / model probs / rules; this just decides.
"""
from __future__ import annotations

from pydantic import BaseModel


class Gate(BaseModel):
    """An asserted, earned-'fact' gating state (away / asleep / empty)."""

    slug: str
    confidence: float = 1.0
    detail: str = ""


class RuleHint(BaseModel):
    slug: str
    confidence: float = 0.55


class ResolveContext(BaseModel):
    """Everything the cascade needs for one window. `gates` are ONLY facts that
    earned bypass (reliability.role_decision == 'fact') AND are currently asserted,
    in precedence order. `blocked` are activity slugs masked out by constraining
    facts (e.g. room occupancy). `model_probs` is None when the model was skipped."""

    override: str | None = None
    gates: list[Gate] = []
    model_probs: dict[str, float] | None = None
    blocked: set[str] = set()
    rule: RuleHint | None = None
    abstain_threshold: float = 0.6


class Resolution(BaseModel):
    predicted: str
    confidence: float
    basis: str               # override | fact | model | rule | unknown
    detail: str = ""
    model_used: bool = False


def needs_model(ctx: ResolveContext) -> bool:
    """False when an override or a gating fact already settles the window — the
    caller can skip feature-build + inference."""
    return ctx.override is None and not ctx.gates


def resolve(ctx: ResolveContext) -> Resolution:
    # 1. manual override — ground truth for its window
    if ctx.override:
        return Resolution(predicted=ctx.override, confidence=1.0, basis="override",
                          detail="manual override", model_used=False)
    # 2. gating fact — away / asleep / empty; first in precedence wins
    if ctx.gates:
        g = ctx.gates[0]
        return Resolution(predicted=g.slug, confidence=g.confidence, basis="fact",
                          detail=g.detail or "ground-truth sensor", model_used=False)
    # 3. masked ML
    if ctx.model_probs:
        probs = {k: v for k, v in ctx.model_probs.items() if k not in ctx.blocked}
        total = sum(probs.values())
        if total > 0:
            probs = {k: v / total for k, v in probs.items()}      # renormalize post-mask
            slug = max(probs, key=probs.get)
            conf = probs[slug]
            if conf >= ctx.abstain_threshold:
                return Resolution(predicted=slug, confidence=round(float(conf), 4),
                                  basis="model", detail="model", model_used=True)
    # 4. rule fallback (cold start / model abstains)
    if ctx.rule:
        return Resolution(predicted=ctx.rule.slug, confidence=ctx.rule.confidence,
                          basis="rule", detail="rule fallback",
                          model_used=ctx.model_probs is not None)
    # 5. abstain
    return Resolution(predicted="unknown", confidence=0.0, basis="unknown",
                      detail="abstained — not confident enough",
                      model_used=ctx.model_probs is not None)

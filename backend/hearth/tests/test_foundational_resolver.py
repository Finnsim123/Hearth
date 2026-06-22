from __future__ import annotations

from hearth.domain.foundational.resolver import (
    Gate,
    ResolveContext,
    RuleHint,
    needs_model,
    resolve,
)


def test_override_wins_over_everything():
    ctx = ResolveContext(override="movie", gates=[Gate(slug="away")],
                         model_probs={"cooking": 0.9})
    r = resolve(ctx)
    assert r.basis == "override" and r.predicted == "movie" and r.confidence == 1.0
    assert r.model_used is False
    assert needs_model(ctx) is False


def test_gating_fact_bypasses_model():
    ctx = ResolveContext(gates=[Gate(slug="away", detail="person is out")],
                         model_probs={"cooking": 0.95})
    r = resolve(ctx)
    assert r.basis == "fact" and r.predicted == "away"
    assert r.model_used is False                 # model skipped — compute saved
    assert needs_model(ctx) is False


def test_gate_precedence_first_wins():
    ctx = ResolveContext(gates=[Gate(slug="away"), Gate(slug="asleep")])
    assert resolve(ctx).predicted == "away"


def test_model_used_when_no_facts():
    ctx = ResolveContext(model_probs={"cooking": 0.7, "eating": 0.3},
                         abstain_threshold=0.6)
    r = resolve(ctx)
    assert r.basis == "model" and r.predicted == "cooking"
    assert needs_model(ctx) is True


def test_abstain_falls_back_to_rule():
    ctx = ResolveContext(model_probs={"cooking": 0.5, "eating": 0.5},
                         rule=RuleHint(slug="home", confidence=0.55),
                         abstain_threshold=0.6)
    r = resolve(ctx)
    assert r.basis == "rule" and r.predicted == "home"
    assert r.model_used is True


def test_abstain_to_unknown_without_rule():
    ctx = ResolveContext(model_probs={"cooking": 0.5, "eating": 0.5},
                         abstain_threshold=0.6)
    r = resolve(ctx)
    assert r.basis == "unknown" and r.predicted == "unknown"


def test_constraining_mask_blocks_argmax():
    # model likes cooking, but room occupancy says not the kitchen → cooking blocked
    ctx = ResolveContext(model_probs={"cooking": 0.6, "reading": 0.4},
                         blocked={"cooking"}, abstain_threshold=0.5)
    r = resolve(ctx)
    assert r.predicted == "reading" and r.basis == "model"


def test_mask_blocking_everything_abstains():
    ctx = ResolveContext(model_probs={"cooking": 0.6, "reading": 0.4},
                         blocked={"cooking", "reading"})
    assert resolve(ctx).basis == "unknown"

"""Abstain / unknown output state (model_levers.md G6; gap analysis G5)."""
from __future__ import annotations

from hearth.domain.inference.output import (
    UNKNOWN, OutputPolicy, apply_abstain, load_output_policy,
)


class _Repo:
    def __init__(self, s=None):
        self.s = s or {}
    def get_setting(self, k, d=None):
        return self.s.get(k, d)


def test_apply_abstain():
    pol = OutputPolicy(abstain_enabled=True, abstain_threshold=0.4)
    assert apply_abstain("movie", 0.9, pol) == "movie"        # confident -> commit
    assert apply_abstain("movie", 0.3, pol) == UNKNOWN        # unsure -> unknown
    off = OutputPolicy(abstain_enabled=False, abstain_threshold=0.4)
    assert apply_abstain("movie", 0.1, off) == "movie"        # disabled -> never abstain


def test_load_output_policy_defaults_and_overrides():
    assert load_output_policy(_Repo()) == OutputPolicy()
    pol = load_output_policy(_Repo({"output.policy": {"abstain_threshold": 0.6,
                                                      "abstain_enabled": False}}))
    assert pol.abstain_threshold == 0.6 and pol.abstain_enabled is False
    # junk / wrong types ignored -> defaults
    bad = load_output_policy(_Repo({"output.policy": {"abstain_threshold": "x",
                                                      "bogus": 1}}))
    assert bad == OutputPolicy()

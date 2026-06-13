"""Model-to-LLM feedback analysis + revision round (llm_layer_design §f)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hearth.domain.onboarding.feature_architect import parse_delta, revision_prompt
from hearth.domain.training.feedback import (
    build_feedback, confusion_unresolved, discriminative_stats, feedback_should_run,
    top_confusions,
)


def test_top_confusions_ranks_offdiagonal():
    conf = {"labels": ["home", "cooking", "eating"],
            "matrix": [[40, 0, 0], [0, 5, 11], [0, 3, 30]]}
    pairs = top_confusions(conf, k=2, min_count=5)
    assert pairs[0] == ("cooking", "eating", 11)          # biggest off-diagonal
    assert all(c >= 5 for _, _, c in pairs)
    assert ("eating", "cooking", 3) not in pairs           # below min_count


def test_discriminative_stats_finds_separating_feature():
    n = 60
    rng = np.random.default_rng(0)
    # 'stove' cleanly separates cooking from eating; 'noise' does not
    X = pd.DataFrame({
        "stove": np.r_[np.ones(n), np.zeros(n)] + rng.normal(0, 0.01, 2 * n),
        "noise": rng.normal(0, 1, 2 * n),
    }, index=pd.RangeIndex(2 * n))
    y = pd.Series(["cooking"] * n + ["eating"] * n, index=X.index)
    ds = discriminative_stats(X, y, [("cooking", "eating", 11)], top_n=2)
    ranked = ds["cooking_vs_eating"]
    assert ranked[0]["feature"] == "stove" and ranked[0]["cohens_d"] > ranked[1]["cohens_d"]


def test_build_feedback_assembles_summary():
    n = 30
    X = pd.DataFrame({"stove": np.r_[np.ones(n), np.zeros(n)],
                      "dead": np.zeros(2 * n)}, index=pd.RangeIndex(2 * n))
    y = pd.Series(["cooking"] * n + ["eating"] * n, index=X.index)
    metrics = {
        "accuracy_confirmed": 0.8, "n_confirmed": 40, "auc_macro": 0.85,
        "per_class": {"cooking": {"f1": 0.5}},
        "confusion": {"labels": ["cooking", "eating"], "matrix": [[20, 10], [8, 22]]},
        "feature_importances": {"stove": 0.7}, "importance_all": {"stove": 0.7},
        "evidence_profile": {"direct": 0.6},
    }
    fb = build_feedback(metrics, X, y)
    assert fb["confusion_top_pairs"][0] == {"true": "cooking", "pred": "eating", "count": 10}
    assert "dead" in fb["feature_importance_zero"]          # zero-importance flagged
    assert "cooking_vs_eating" in fb["discriminative_stats"]
    assert fb["validation"]["n_confirmed"] == 40


def test_stopping_criteria():
    assert feedback_should_run(40) is True
    assert feedback_should_run(10) is False                 # too few confirmed labels
    busy = {"labels": ["a", "b"], "matrix": [[10, 9], [2, 10]]}
    calm = {"labels": ["a", "b"], "matrix": [[10, 1], [0, 10]]}
    assert confusion_unresolved(busy) is True
    assert confusion_unresolved(calm, floor=5) is False     # nothing worth a round


def test_parse_delta():
    add, drop = parse_delta({
        "add": [{"name": "stove_on", "transform": "any_active",
                 "inputs": ["binary_sensor.stove"], "info_tier": "T1"}],
        "drop": ["hallway_lux_max", 123]})
    assert [f.name for f in add] == ["stove_on"]
    assert drop == ["hallway_lux_max", "123"]
    assert parse_delta("nonsense") == ([], [])


def test_revision_prompt_injects_feedback_and_whitelist():
    p = revision_prompt({"confusion_top_pairs": [{"true": "cooking", "pred": "eating"}]},
                        mode="full")
    assert "cooking" in p and "occupancy_fraction" in p and "window_slope" in p

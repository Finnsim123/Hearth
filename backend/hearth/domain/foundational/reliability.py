"""Reliability gate — does a foundational sensor deserve to BYPASS the model?

A 'fact' is only as good as its sensor (foundational_facts_design §7a). This scores
a candidate from its own history with three label-free-friendly checks and routes it:

    high score → 'fact'      (bypass the model — gate/override)
    medium     → 'feature'   (fed to the model; it learns when to trust it)
    low/broken → 'suspect'   (flagged; maybe excluded)

Checks, cheapest first:
 1. plausibility   — does the signal BEHAVE like what it claims? (role-specific)
 2. corroboration  — does it agree with the rest of the home? (contradiction rate)
 3. label agreement— precision/recall vs confirmed truth (when labels exist)

Pure: pandas in, a verdict out. No I/O, no role registry lookups — the caller picks
a RoleProfile. Mirrors the spirit of onboarding.heuristic_reliability, one level up.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

FACT_THRESHOLD = 0.80
FEATURE_THRESHOLD = 0.45


class RoleProfile(BaseModel):
    """What 'plausible' means for one kind of foundational sensor, and how readily
    it may become a fact. PRESENCE clears a trivial bar (inherently reliable);
    SLEEP must earn it (bed sensors are noisy)."""

    name: str
    requires_night_block: bool = False
    night_hours: tuple[int, int] = (22, 8)      # [start, end) local hour
    min_block_min: float = 0.0                   # expected contiguous 'asserted' run
    max_flips_per_day: float = 24.0              # above this = flapping/noise
    min_night_frac: float = 0.0                  # share of asserted time in night
    fact_eligible_default: bool = False
    min_observation_days: float = 5.0


SLEEP = RoleProfile(name="sleep", requires_night_block=True, night_hours=(22, 8),
                    min_block_min=180, max_flips_per_day=8, min_night_frac=0.6,
                    fact_eligible_default=False, min_observation_days=5.0)
PRESENCE = RoleProfile(name="presence", requires_night_block=False,
                       min_block_min=5, max_flips_per_day=24,
                       fact_eligible_default=True, min_observation_days=3.0)


class ReliabilityVerdict(BaseModel):
    role_decision: str               # fact | feature | suspect
    score: float                     # 0..1 composite
    eligible: bool                   # enough observation to decide
    observation_days: float
    checks: dict                     # sub-scores + raw metrics
    reason: str


# ── primitives ──────────────────────────────────────────────────────────────
def _asserted_runs(mask: np.ndarray) -> list[int]:
    """Lengths (in samples) of contiguous True runs."""
    runs, cur = [], 0
    for v in mask:
        if v:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    return runs


def _freq_min(idx: pd.DatetimeIndex) -> float:
    if len(idx) < 2:
        return 1.0
    d = pd.Series(idx).diff().dropna().dt.total_seconds().median()
    return max(1.0, float(d) / 60.0)


def plausibility(fact: pd.Series, profile: RoleProfile) -> dict:
    """Does the signal behave like the thing it claims? Returns sub-score + metrics."""
    n = len(fact)
    missing_frac = float(fact.isna().mean()) if n else 1.0
    f = fact.dropna().astype(bool)
    if f.empty:
        return {"score": 0.0, "missing_frac": round(missing_frac, 3), "stuck": True,
                "flips_per_day": 0.0, "median_block_min": 0.0, "night_frac": 0.0}
    vals = f.to_numpy()
    idx = f.index
    freq = _freq_min(idx)
    span_days = max((idx[-1] - idx[0]).total_seconds() / 86400, freq / 1440)
    transitions = int((vals[1:] != vals[:-1]).sum()) if len(vals) > 1 else 0
    flips_per_day = transitions / span_days if span_days else 0.0
    runs = _asserted_runs(vals)
    median_block_min = float(np.median(runs) * freq) if runs else 0.0
    stuck = bool(vals.all() or (not vals.any()))    # all True or all False = no signal
    # night alignment
    hour = idx.hour
    s, e = profile.night_hours
    night = (hour >= s) | (hour < e) if s > e else (hour >= s) & (hour < e)
    asserted = vals.sum()
    night_frac = float((vals & night).sum() / asserted) if asserted else 0.0

    comps = []
    comps.append(1.0 - missing_frac)                       # uptime
    comps.append(0.0 if stuck else 1.0)                    # not frozen / not constant
    flip_ok = 1.0 if flips_per_day <= profile.max_flips_per_day else \
        max(0.0, 1.0 - (flips_per_day - profile.max_flips_per_day) / (profile.max_flips_per_day * 3))
    comps.append(flip_ok)
    if profile.requires_night_block:
        comps.append(min(1.0, median_block_min / profile.min_block_min) if profile.min_block_min else 1.0)
        comps.append(min(1.0, night_frac / profile.min_night_frac) if profile.min_night_frac else 1.0)
    score = float(np.mean(comps)) if comps else 0.0
    return {"score": round(score, 3), "missing_frac": round(missing_frac, 3),
            "stuck": stuck, "flips_per_day": round(flips_per_day, 1),
            "median_block_min": round(median_block_min, 1),
            "night_frac": round(night_frac, 3)}


def corroboration(fact: pd.Series, contradiction: pd.Series) -> dict:
    """`contradiction` is True where OTHER signals imply the fact is FALSE
    (e.g. 'asleep' but TV on + daytime). Score = 1 - rate over asserted windows."""
    f = fact.fillna(False).astype(bool)
    c = contradiction.reindex(f.index).fillna(False).astype(bool)
    asserted = f.sum()
    if asserted == 0:
        return {"score": 1.0, "contradiction_rate": 0.0, "n_asserted": 0}
    rate = float((f & c).sum() / asserted)
    return {"score": round(1.0 - rate, 3), "contradiction_rate": round(rate, 3),
            "n_asserted": int(asserted)}


def label_agreement(fact: pd.Series, truth: pd.Series) -> dict:
    """Precision/recall/F1 of the asserted fact vs confirmed truth, on windows where
    truth is known. Gold standard once the feedback loop has labels."""
    t = truth.dropna().astype(bool)
    if t.empty:
        return {"score": None, "precision": None, "recall": None, "n": 0}
    f = fact.reindex(t.index).fillna(False).astype(bool)
    tp = int((f & t).sum()); fp = int((f & ~t).sum()); fn = int((~f & t).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"score": round(f1, 3), "precision": round(precision, 3),
            "recall": round(recall, 3), "n": int(t.sum() + (~t).sum())}


def score_foundational(fact: pd.Series, profile: RoleProfile, *,
                       contradiction: pd.Series | None = None,
                       truth: pd.Series | None = None) -> ReliabilityVerdict:
    """Combine the available checks into a verdict. Weighting prefers ground-truth
    labels when present, then corroboration, then plausibility."""
    plaus = plausibility(fact, profile)
    corr = corroboration(fact, contradiction) if contradiction is not None else None
    lab = label_agreement(fact, truth) if truth is not None else None

    if lab and lab["score"] is not None:
        score = 0.5 * lab["score"] + 0.3 * (corr["score"] if corr else plaus["score"]) \
            + 0.2 * plaus["score"]
    elif corr is not None:
        score = 0.6 * plaus["score"] + 0.4 * corr["score"]
    else:
        score = plaus["score"]
    score = round(float(score), 3)

    f = fact.dropna()
    obs_days = ((f.index[-1] - f.index[0]).total_seconds() / 86400) if len(f) > 1 else 0.0
    eligible = obs_days >= profile.min_observation_days

    if not eligible:
        decision = "feature"                       # interim: a hint while we watch
    elif score >= FACT_THRESHOLD:
        decision = "fact"
    elif score >= FEATURE_THRESHOLD:
        decision = "feature"
    else:
        decision = "suspect"

    checks = {"plausibility": plaus, "corroboration": corr, "label": lab}
    return ReliabilityVerdict(role_decision=decision, score=score, eligible=eligible,
                              observation_days=round(obs_days, 2), checks=checks,
                              reason=_reason(decision, score, eligible, obs_days, profile, plaus, corr))


def _reason(decision, score, eligible, obs_days, profile, plaus, corr) -> str:
    if not eligible:
        return (f"watching for now — {obs_days:.1f}d of data, need "
                f"{profile.min_observation_days:.0f}d before I trust it as a fact; "
                "using it as a hint.")
    bits = []
    if plaus["flips_per_day"] > profile.max_flips_per_day:
        bits.append(f"flips {plaus['flips_per_day']:.0f}×/day")
    if plaus["stuck"]:
        bits.append("never changes")
    if plaus["missing_frac"] > 0.2:
        bits.append(f"{plaus['missing_frac']:.0%} missing")
    if corr and corr["contradiction_rate"] > 0.1:
        bits.append(f"contradicts other signals {corr['contradiction_rate']:.0%} of the time")
    why = "; ".join(bits) if bits else "behaves consistently and agrees with the home"
    if decision == "fact":
        return f"reliable ({why}) — treating it as known, bypassing the model."
    if decision == "feature":
        return f"not reliable enough to be a fact ({why}) — using it as a hint to the model."
    return f"unreliable ({why}) — flagged; not used as a fact."

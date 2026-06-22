"""Foundational facts — config, signal extraction, and the verdict job.

A FoundationalFact binds a sensor (by its feature prefix = Binding.name) to a gate
state (away / asleep). The verdict job scores each one with the reliability gate
(reliability.py) so only sensors that EARN it get to bypass the model; the rest are
demoted to features/hints. Stored in settings (no DB migration):
  foundational.facts    -> list[FoundationalFact]
  foundational.verdicts -> {fact_id: ReliabilityVerdict}

Column conventions come straight from the recipe registry:
  PERSON role → '{name}_home_last' (1 home · 0 away · -1 unknown)
  BED    role → '{name}_occupied'  (1 in bed · 0 not · -1 absent)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from pydantic import BaseModel

from ..schemas import Role
from .reliability import PRESENCE, SLEEP, ReliabilityVerdict, score_foundational

AWAY = "away"
ASLEEP = "asleep"
_GATE_PROFILE = {AWAY: PRESENCE, ASLEEP: SLEEP}
_GATE_ROLE = {AWAY: Role.PERSON, ASLEEP: Role.BED}


class FoundationalFact(BaseModel):
    id: str                         # stable key, e.g. "alice:asleep"
    gate: str                       # away | asleep
    binding_name: str               # the driving sensor's feature prefix
    role: Role
    person_id: str | None = None
    enabled: bool = True


# ── persistence (settings) ───────────────────────────────────────────────────
def load_facts(repo) -> list[FoundationalFact]:
    raw = repo.get_setting("foundational.facts") or []
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(FoundationalFact(**d))
        except Exception:
            continue
    return out


def save_facts(repo, facts: list[FoundationalFact]) -> None:
    repo.set_setting("foundational.facts", [f.model_dump(mode="json") for f in facts])


def load_verdicts(repo) -> dict:
    return repo.get_setting("foundational.verdicts") or {}


def save_verdicts(repo, verdicts: dict) -> None:
    repo.set_setting("foundational.verdicts", verdicts)


# ── signal extraction ────────────────────────────────────────────────────────
def fact_series(feats: pd.DataFrame, fact: FoundationalFact) -> pd.Series:
    """Boolean series: is the gate ASSERTED in each window? Unknown (-1) is False."""
    if fact.gate == AWAY:
        col = f"{fact.binding_name}_home_last"
        return (feats[col] == 0.0) if col in feats.columns else pd.Series(False, index=feats.index)
    if fact.gate == ASLEEP:
        col = f"{fact.binding_name}_occupied"
        return (feats[col] > 0.5) if col in feats.columns else pd.Series(False, index=feats.index)
    return pd.Series(False, index=feats.index)


def _awake_evidence(feats: pd.DataFrame) -> pd.Series:
    """True where signals imply someone is AWAKE/active — contradicts 'asleep'."""
    idx = feats.index
    awake = pd.Series(False, index=idx)
    if "time_bucket" in feats.columns:              # 0 = night (pipeline _bucket)
        awake = awake | (feats["time_bucket"] != 0)
    else:
        awake = awake | ((idx.hour >= 8) & (idx.hour < 22))
    for c in feats.columns:
        if c.endswith("_on_frac") or c.endswith("_on_last"):       # lights on
            awake = awake | (feats[c] > 0.3)
        elif c.endswith("_active") or c.endswith("_playing"):       # media playing
            awake = awake | (feats[c] > 0.5)
    return awake


def contradiction_series(feats: pd.DataFrame, fact: FoundationalFact) -> pd.Series | None:
    if fact.gate == ASLEEP:
        return _awake_evidence(feats)
    return None                                     # presence corroboration is trivial


def compute_verdict(feats: pd.DataFrame, fact: FoundationalFact) -> ReliabilityVerdict:
    profile = _GATE_PROFILE.get(fact.gate, SLEEP)
    fseries = fact_series(feats, fact).astype(float)
    contra = contradiction_series(feats, fact)
    return score_foundational(fseries, profile, contradiction=contra)


# ── the verdict job (scheduler / API) ────────────────────────────────────────
def run_verdicts(tsdb, repo, days: int = 14) -> dict:
    """Score every enabled foundational fact from recent history and store the
    verdicts. Reuse the drift cadence. Returns {fact_id: verdict dict}."""
    from ..features.registry import active_feature_set_version
    fset = active_feature_set_version(repo)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    verdicts = load_verdicts(repo)
    for f in load_facts(repo):
        if not f.enabled:
            continue
        feats = tsdb.read_features(f.person_id or "", fset, start, end) \
            if f.person_id else tsdb.read_features("", fset, start, end)
        if feats is None or feats.empty:
            continue
        try:
            v = compute_verdict(feats, f)
            verdicts[f.id] = v.model_dump(mode="json")
        except Exception:
            continue
    save_verdicts(repo, verdicts)
    return verdicts


def candidate_bindings(repo, gate: str) -> list[dict]:
    """Sensors a user could bind to this gate (by role). For the wizard/Settings."""
    role = _GATE_ROLE.get(gate)
    return [{"binding_name": b.name, "entity_id": b.entity_id, "room": b.room,
             "person_id": b.person_id}
            for b in repo.bindings() if role is not None and b.role == role]


def extra_gate_slugs(repo, person_id: str) -> list[FoundationalFact]:
    """Enabled, person-scoped facts (excluding away, handled by presence.py) whose
    verdict has EARNED fact status — these add gates in the predictor."""
    verdicts = load_verdicts(repo)
    out = []
    for f in load_facts(repo):
        if not f.enabled or f.gate == AWAY:
            continue
        if f.person_id not in (None, person_id):
            continue
        if verdicts.get(f.id, {}).get("role_decision") == "fact":
            out.append(f)
    return out

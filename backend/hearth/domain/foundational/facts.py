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


# steps accrued within a window that clearly mean "up and moving about" (a couple
# of bathroom steps shouldn't flag the whole window as awake).
STEP_AWAKE_STEPS = 60.0


def _awake_evidence(feats: pd.DataFrame) -> pd.Series:
    """True where signals imply someone is AWAKE/active — contradicts 'asleep'.

    Two tiers: HARD evidence (lights on, media playing, real step movement) is
    unambiguous; the SOFT time-of-day prior (it's daytime) is weak and can be
    cancelled when the phone says you're resting (on the charger and not moving),
    so a daytime nap with a parked phone isn't wrongly counted against the sleep
    sensor. Fully backward-compatible: with no steps/charging columns this reduces
    to the previous time-of-day-OR-lights-OR-media behaviour."""
    idx = feats.index
    if "time_bucket" in feats.columns:              # 0 = night (pipeline _bucket)
        soft = (feats["time_bucket"] != 0)
    else:
        soft = pd.Series((idx.hour >= 8) & (idx.hour < 22), index=idx)
    hard = pd.Series(False, index=idx)
    for c in feats.columns:
        cl = c.lower()
        if c.endswith("_on_frac") or c.endswith("_on_last"):       # lights on
            hard = hard | (feats[c] > 0.3)
        elif c.endswith("_active") or c.endswith("_playing"):       # media playing
            hard = hard | (feats[c] > 0.5)
        elif "step" in cl and c.endswith("_delta"):                 # real movement
            hard = hard | (feats[c].fillna(0.0) > STEP_AWAKE_STEPS)
    rest = _charging_rest(feats)
    return hard | (soft & ~rest)


def _charging_rest(feats: pd.DataFrame) -> pd.Series:
    """True where the phone is on the charger AND there's no step movement — a
    'resting' signal that suppresses the weak daytime-awake prior. Detects a
    binary charging sensor (any column with 'charg') or a battery level that is
    RISING ({batt}_delta > 0). Returns all-False when no such sensor is bound."""
    idx = feats.index
    charging = pd.Series(False, index=idx)
    found = False
    for c in feats.columns:
        cl = c.lower()
        if "charg" in cl:
            charging = charging | (feats[c].fillna(0.0) > 0.5); found = True
        elif "batt" in cl and c.endswith("_delta"):
            charging = charging | (feats[c].fillna(0.0) > 0.0); found = True
    if not found:
        return pd.Series(False, index=idx)
    still = pd.Series(True, index=idx)
    for c in feats.columns:
        if "step" in c.lower() and c.endswith("_delta"):
            still = still & (feats[c].fillna(0.0) <= STEP_AWAKE_STEPS)
    return charging & still


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
    verdicts. Reuse the drift cadence. Returns {fact_id: verdict dict}.

    Side effect (transparency): when a sensor's role_decision CHANGES — especially a
    demotion out of 'fact' status — record an advisory + a timeline event so the user
    learns a fact they depend on has become unreliable (or has re-earned trust)."""
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
            prev = (verdicts.get(f.id) or {}).get("role_decision")
            v = compute_verdict(feats, f)
            verdicts[f.id] = v.model_dump(mode="json")
            _announce_verdict_change(repo, f, prev, v)
        except Exception:
            continue
    save_verdicts(repo, verdicts)
    return verdicts


def _announce_verdict_change(repo, fact: FoundationalFact, prev: str | None,
                             v: ReliabilityVerdict) -> None:
    """Advisory + event on a meaningful change in a sensor's reliability role."""
    new = v.role_decision
    if prev is None or prev == new:
        return
    from .. import advisories, events
    name = fact.binding_name
    gate = fact.gate
    kind = f"foundational:{fact.id}"
    if prev == "fact" and new in ("feature", "suspect"):
        events.record_event(repo, "sensor_demoted",
                            f"{name} demoted from a trusted '{gate}' fact",
                            v.reason)
        advisories.record_advisory(
            repo, kind, f"{name} is no longer reliable for '{gate}'",
            v.reason + " I've stopped treating it as known and I'm using it as a hint.",
            severity="warn", cta={"label": "Review", "href": "/settings#model"})
    elif new == "fact" and prev in ("feature", "suspect"):
        events.record_event(repo, "sensor_promoted",
                            f"{name} earned trusted '{gate}' fact status", v.reason)
        advisories.clear_advisory(repo, kind)


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

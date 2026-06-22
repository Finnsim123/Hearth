"""Governor — homeostasis. Turns the heaviness index into a state (with
hysteresis, like the prediction smoother) and a degradation plan that says what
work to shed. Pure: callers (scheduler) ask `admit(kind, state)` before running a
heavy job; the trainer calls `should_yield` between stages.

The invariant the whole ladder protects: live inference is NEVER shed. Only
learning/maintenance work degrades.
"""
from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel

from .vitals import GovernorConfig, Vitals, heaviness_index


class GovernorState(IntEnum):
    """Ordered so comparisons mean 'more severe'. IntEnum → NORMAL < CRITICAL."""

    NORMAL = 0
    ELEVATED = 1
    HIGH = 2
    CRITICAL = 3


# job kinds the scheduler tags work with; inference is special (never shed)
INFERENCE = "inference"
TRAINING = "training"
TUNING = "tuning"
DISCOVERY = "discovery"
IMPORT = "import"


class DegradationPlan(BaseModel):
    """What a state permits. `admitted` lists job kinds allowed to start now;
    n_jobs_cap caps parallelism (None = unlimited); interval_multiplier widens
    scheduler cadences; influx_chunk_factor shrinks query slices under load."""

    state: GovernorState
    admitted: set[str]
    n_jobs_cap: int | None = None
    interval_multiplier: float = 1.0
    influx_chunk_factor: float = 1.0
    pause_training: bool = False
    reason: str = ""


_PLANS: dict[GovernorState, DegradationPlan] = {
    GovernorState.NORMAL: DegradationPlan(
        state=GovernorState.NORMAL,
        admitted={INFERENCE, TRAINING, TUNING, DISCOVERY, IMPORT},
        reason="full speed"),
    GovernorState.ELEVATED: DegradationPlan(
        state=GovernorState.ELEVATED,
        admitted={INFERENCE, TRAINING, IMPORT},
        interval_multiplier=1.0,
        reason="deferring optional work (discovery, tuning)"),
    GovernorState.HIGH: DegradationPlan(
        state=GovernorState.HIGH,
        admitted={INFERENCE, IMPORT},
        n_jobs_cap=1, interval_multiplier=2.0, influx_chunk_factor=0.5,
        pause_training=True,
        reason="reduce parallelism, chunk Influx, pause training"),
    GovernorState.CRITICAL: DegradationPlan(
        state=GovernorState.CRITICAL,
        admitted={INFERENCE},
        n_jobs_cap=1, interval_multiplier=4.0, influx_chunk_factor=0.25,
        pause_training=True,
        reason="safe mode: inference only; halt ingest/backfill"),
}


def plan_for(state: GovernorState) -> DegradationPlan:
    return _PLANS[state]


def admit(job_kind: str, state: GovernorState) -> bool:
    """May a job of this kind start in this state? Unknown kinds are treated as
    heavy (admitted only at NORMAL) — fail safe."""
    plan = _PLANS[state]
    if job_kind in plan.admitted:
        return True
    return job_kind not in _PLANS[GovernorState.NORMAL].admitted and state == GovernorState.NORMAL


def decide_state(prev: GovernorState | None, v: Vitals,
                 cfg: GovernorConfig | None = None) -> tuple[GovernorState, float]:
    """Map the current Vitals (+ previous state, for hysteresis) to a state.
    Returns (state, heaviness). Hard safety triggers (thermal ceiling, disk
    floor) jump straight to CRITICAL and hold through a cooldown."""
    cfg = cfg or GovernorConfig()
    prev = GovernorState.NORMAL if prev is None else prev
    h = heaviness_index(v, cfg)

    # ── hard triggers: thermal ceiling or disk floor → CRITICAL immediately ──
    # Disk must be UNKNOWN-safe: a monitor that can't read the volume (psutil
    # missing, bad data_path) returns the 0.0 default, and "unknown" must never
    # be read as "0 GB free = full". A real near-full disk reports free < floor
    # AND non-zero usage; an unread one is free==0 AND used==0 — skip that.
    disk_known = v.disk_free_gb > 0.0 or v.disk_used_pct > 0.0
    disk_floor = disk_known and v.disk_free_gb < cfg.min_disk_gb
    if (v.temp_c is not None and v.temp_c >= cfg.temp_max) or disk_floor:
        return GovernorState.CRITICAL, h
    # thermal cooldown: once CRITICAL on heat, hold until back below temp_warn
    if prev == GovernorState.CRITICAL and v.temp_c is not None and v.temp_c > cfg.temp_warn:
        return GovernorState.CRITICAL, h

    # ── candidate purely from heaviness (ascending enter thresholds) ──
    if h >= cfg.enter_critical:
        cand = GovernorState.CRITICAL
    elif h >= cfg.enter_high:
        cand = GovernorState.HIGH
    elif h >= cfg.enter_elevated:
        cand = GovernorState.ELEVATED
    else:
        cand = GovernorState.NORMAL

    # ── hysteresis: never step DOWN a level unless clearly below its enter edge.
    # (Stepping up is immediate; stepping down needs `leave_margin` of slack.)
    if cand < prev:
        enter_edge = {
            GovernorState.ELEVATED: cfg.enter_elevated,
            GovernorState.HIGH: cfg.enter_high,
            GovernorState.CRITICAL: cfg.enter_critical,
        }.get(prev, 0.0)
        if h > enter_edge - cfg.leave_margin:
            return prev, h   # hold the higher state — avoid flapping
    return cand, h


def should_yield(state: GovernorState) -> bool:
    """Trainer stage-boundary check: at HIGH/CRITICAL a long job should pause or
    abort cleanly between stages rather than be killed mid-fit."""
    return state >= GovernorState.HIGH

from __future__ import annotations

from hearth.domain.system.governor import (
    DISCOVERY,
    INFERENCE,
    TRAINING,
    GovernorState,
    admit,
    decide_state,
    plan_for,
    should_yield,
)
from hearth.domain.system.vitals import GovernorConfig, Vitals, heaviness_index

CFG = GovernorConfig()


def V(**kw) -> Vitals:
    """Vitals with a healthy disk by default, so disk-floor doesn't fire in tests
    that are exercising the heaviness/thermal paths."""
    kw.setdefault("disk_free_gb", 50.0)
    return Vitals(**kw)


def test_heaviness_is_worst_headroom():
    v = Vitals(cpu_pct=90, mem_pct=40, disk_used_pct=30)
    assert abs(heaviness_index(v, CFG) - 0.90) < 1e-9


def test_heaviness_ignores_missing_temp():
    assert heaviness_index(Vitals(cpu_pct=50, temp_c=None), CFG) == 0.5


def test_heaviness_thermal_interpolation():
    # temp halfway between warn (70) and max (80) → 0.5 contribution
    v = Vitals(cpu_pct=10, temp_c=75)
    assert abs(heaviness_index(v, CFG) - 0.5) < 1e-9


def test_states_by_band():
    assert decide_state(None, V(cpu_pct=10))[0] == GovernorState.NORMAL
    assert decide_state(None, V(cpu_pct=72))[0] == GovernorState.ELEVATED
    assert decide_state(None, V(cpu_pct=88))[0] == GovernorState.HIGH
    assert decide_state(None, V(cpu_pct=97))[0] == GovernorState.CRITICAL


def test_hysteresis_holds_then_releases():
    # in HIGH, a dip to 0.80 (below enter_high 0.85 but within leave_margin) holds
    state, _ = decide_state(GovernorState.HIGH, V(cpu_pct=80))
    assert state == GovernorState.HIGH
    # a clear drop to 0.77 (< 0.85 - 0.07) steps down
    state, _ = decide_state(GovernorState.HIGH, V(cpu_pct=77))
    assert state == GovernorState.ELEVATED


def test_step_up_is_immediate():
    state, _ = decide_state(GovernorState.NORMAL, V(cpu_pct=97))
    assert state == GovernorState.CRITICAL


def test_thermal_ceiling_forces_critical():
    state, _ = decide_state(GovernorState.NORMAL, V(cpu_pct=5, temp_c=85))
    assert state == GovernorState.CRITICAL


def test_thermal_cooldown_holds_until_below_warn():
    # prev CRITICAL, still warm (72 > warn 70) → hold even though heaviness is low
    state, _ = decide_state(GovernorState.CRITICAL, V(cpu_pct=5, temp_c=72))
    assert state == GovernorState.CRITICAL
    # cooled below warn → released
    state, _ = decide_state(GovernorState.CRITICAL, V(cpu_pct=5, temp_c=68))
    assert state == GovernorState.NORMAL


def test_disk_floor_forces_critical():
    state, _ = decide_state(None, Vitals(cpu_pct=5, disk_free_gb=0.4))
    assert state == GovernorState.CRITICAL


def test_admission_ladder_protects_inference():
    for s in GovernorState:
        assert admit(INFERENCE, s) is True            # never shed
    assert admit(TRAINING, GovernorState.ELEVATED) is True
    assert admit(TRAINING, GovernorState.HIGH) is False
    assert admit(DISCOVERY, GovernorState.NORMAL) is True
    assert admit(DISCOVERY, GovernorState.ELEVATED) is False


def test_plan_and_yield():
    assert plan_for(GovernorState.HIGH).pause_training is True
    assert plan_for(GovernorState.CRITICAL).admitted == {INFERENCE}
    assert should_yield(GovernorState.HIGH) is True
    assert should_yield(GovernorState.ELEVATED) is False


def test_unknown_disk_is_not_critical():
    """A monitor that can't read the volume returns disk_free_gb=0.0; that
    "unknown" must NOT be read as a full disk (the false-CRITICAL / stuck-heavy
    bug). Only a real reading (non-zero usage) trips the floor."""
    from hearth.domain.system.governor import GovernorState, decide_state
    from hearth.domain.system.vitals import Vitals
    # empty snapshot (psutil missing / bad path) → NORMAL, not CRITICAL
    assert decide_state(GovernorState.NORMAL, Vitals())[0] == GovernorState.NORMAL
    # a genuinely near-full disk (free<1GB AND used reported) → CRITICAL
    full = Vitals(disk_free_gb=0.3, disk_used_pct=99.0)
    assert decide_state(GovernorState.NORMAL, full)[0] == GovernorState.CRITICAL

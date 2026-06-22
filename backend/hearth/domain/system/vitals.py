"""Vitals — the read-only sensing layer + the single 'heaviness' index.

A Vitals snapshot is the worst-case-headroom view of the box at one instant; the
heaviness index collapses it to one 0–1 number the governor gates on. Pure
domain: the actual reads live in adapters (psutil_monitor); here we only define
the shape, the Protocols adapters implement, and the math.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field


class Vitals(BaseModel):
    """One instant of system state. Optional fields degrade to None when the host
    can't expose them (e.g. no temperature sensor, no power meter) — the index
    simply ignores what it can't see."""

    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cpu_pct: float = 0.0            # 0..100, whole-box utilisation
    load1: float = 0.0             # 1-min load average
    mem_pct: float = 0.0           # 0..100 system memory used
    swap_pct: float = 0.0          # 0..100 swap used — swapping is a red flag
    temp_c: float | None = None    # hottest core/package, °C
    disk_free_gb: float = 0.0
    disk_used_pct: float = 0.0     # 0..100 on the data volume
    watts: float | None = None     # instantaneous power, if measurable
    influx_query_load: float = 0.0  # 0..1 query-pressure estimate (InfluxHealth)
    process_rss_mb: float = 0.0    # Hearth's own resident memory


class GovernorConfig(BaseModel):
    """Every threshold in one editable place (mirrors TrainingConfig). Defaults
    are the design's; everything is overridable via the 'system.governor' setting."""

    # thermal (°C) — first-class because a fanless Pi/NUC throttles or shuts down
    temp_warn: float = 70.0
    temp_max: float = 80.0
    # heaviness band edges (enter thresholds); leaving uses `leave_margin` below
    enter_elevated: float = 0.70
    enter_high: float = 0.85
    enter_critical: float = 0.95
    leave_margin: float = 0.07     # hysteresis: must fall this far below to step down
    # hard safety floors
    min_disk_gb: float = 1.0
    # weighting knob: swap counts heavier (swapping ≈ about to fall over)
    swap_weight: float = 1.5


def heaviness_index(v: Vitals, cfg: GovernorConfig | None = None) -> float:
    """Collapse a Vitals snapshot to one 0–1 'how heavy am I' number = the worst
    headroom across resources. Missing signals are skipped, never assumed busy."""
    cfg = cfg or GovernorConfig()
    parts = [
        v.cpu_pct / 100.0,
        v.mem_pct / 100.0,
        v.disk_used_pct / 100.0,
        min(1.0, (v.swap_pct / 100.0) * cfg.swap_weight),
        max(0.0, min(1.0, v.influx_query_load)),
    ]
    if v.temp_c is not None and cfg.temp_max > cfg.temp_warn:
        parts.append((v.temp_c - cfg.temp_warn) / (cfg.temp_max - cfg.temp_warn))
    return float(min(1.0, max(0.0, max(parts))))


# ── Ports (adapters implement these; kept here to avoid a ports.py edit, can be
#    moved into domain/ports.py later) ─────────────────────────────────────────
class ResourceMonitor(Protocol):
    """Reads host + process resource state (adapter: psutil_monitor)."""

    def sample(self) -> Vitals: ...


class PowerMeter(Protocol):
    """Instantaneous watts, if the box can measure or estimate it (RAPL / Pi /
    smart-plug-via-HA / modelled). Returns None when unknown."""

    def watts(self, cpu_pct: float) -> float | None: ...


class InfluxHealth(Protocol):
    """Query/write/cardinality/disk pressure for InfluxDB (adapter: influx_store)."""

    def snapshot(self) -> dict: ...
    def query_load(self) -> float:
        """0..1 estimate of current query pressure (feeds the heaviness index)."""
        ...

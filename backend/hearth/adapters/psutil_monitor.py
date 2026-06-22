"""ResourceMonitor + PowerMeter adapters (domain.system ports).

Local-first sensing with zero extra services: psutil for host/process state,
/sys/class/powercap (Intel RAPL) for real energy when present, else a modelled
estimate. Everything is best-effort — a missing sensor yields None, never a crash,
so the governor degrades gracefully on any box (NUC, NAS, Pi).
"""
from __future__ import annotations

import logging
import time

from ..domain.system.vitals import Vitals

log = logging.getLogger(__name__)


class PsutilResourceMonitor:
    """Implements domain.system.vitals.ResourceMonitor."""

    def __init__(self, data_path: str = "/data", influx_health=None):
        self._path = data_path
        self._influx = influx_health           # optional InfluxHealth for query load
        self._power = RaplPowerMeter() or None  # truthy check via __bool__ below
        if not self._power:
            self._power = EstimatedPowerMeter()

    def sample(self) -> Vitals:
        v = Vitals()
        try:
            import psutil
        except Exception:
            return v                           # psutil absent → empty snapshot
        try:
            v.cpu_pct = float(psutil.cpu_percent(interval=None))
            try:
                v.load1 = float(psutil.getloadavg()[0])
            except (AttributeError, OSError):
                pass
            vm = psutil.virtual_memory()
            v.mem_pct = float(vm.percent)
            sw = psutil.swap_memory()
            v.swap_pct = float(sw.percent)
            du = psutil.disk_usage(self._path)
            v.disk_free_gb = round(du.free / 1e9, 2)
            v.disk_used_pct = float(du.percent)
            try:
                v.process_rss_mb = round(psutil.Process().memory_info().rss / 1e6, 1)
            except Exception:
                pass
            v.temp_c = _read_temp(psutil)
        except Exception:
            log.exception("psutil sample failed (partial vitals returned)")
        v.watts = self._power.watts(v.cpu_pct) if self._power else None
        if self._influx is not None:
            try:
                v.influx_query_load = float(self._influx.query_load())
            except Exception:
                pass
        return v


def _read_temp(psutil) -> float | None:
    """Hottest core/package across whatever the platform exposes. Falls back to
    the Raspberry Pi thermal-zone file when psutil has no sensors."""
    try:
        temps = psutil.sensors_temperatures()
        readings = [t.current for group in temps.values() for t in group
                    if getattr(t, "current", None) is not None]
        if readings:
            return round(max(readings), 1)
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


class RaplPowerMeter:
    """Intel RAPL energy via /sys/class/powercap. watts() differences the energy
    counter between calls. __bool__ is False when no RAPL domain exists, so the
    monitor can fall back to the estimate."""

    _ROOT = "/sys/class/powercap/intel-rapl:0/energy_uj"

    def __init__(self) -> None:
        self._ok = False
        self._last_uj: int | None = None
        self._last_t: float | None = None
        try:
            self._read_uj()
            self._ok = True
        except Exception:
            self._ok = False

    def __bool__(self) -> bool:
        return self._ok

    def _read_uj(self) -> int:
        with open(self._ROOT) as f:
            return int(f.read().strip())

    def watts(self, cpu_pct: float) -> float | None:
        try:
            now, uj = time.monotonic(), self._read_uj()
        except Exception:
            return None
        if self._last_uj is None or self._last_t is None or now <= self._last_t:
            self._last_uj, self._last_t = uj, now
            return None
        dj = (uj - self._last_uj) / 1e6                 # µJ → J
        dt = now - self._last_t
        self._last_uj, self._last_t = uj, now
        if uj < self._last_uj or dt <= 0:               # counter wrapped
            return None
        return round(dj / dt, 1)


class EstimatedPowerMeter:
    """CodeCarbon-style fallback: power ≈ idle + cpu_fraction × (TDP). Coarse but
    monotone with load, which is all the governor and the energy panel need."""

    def __init__(self, idle_w: float = 8.0, cpu_tdp_w: float = 28.0):
        self._idle = idle_w
        self._tdp = cpu_tdp_w

    def watts(self, cpu_pct: float) -> float | None:
        return round(self._idle + (max(0.0, min(100.0, cpu_pct)) / 100.0) * self._tdp, 1)

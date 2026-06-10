"""TimeSeriesStore adapter — InfluxDB 2.x (ADR-3).

Schema in docs/DATA_MODEL.md §1. Invariants enforced HERE so domain code can't
recreate the prototype's failure modes:
- one value type per measurement (num XOR str field) — no Flux type collisions
- writes are batched + synchronous flush per job tick
- slow-sensor reads automatically extend lookback (role metadata)
- single client instance, injected; never constructed in domain code
"""
from __future__ import annotations


class InfluxStore:
    """Implements domain.ports.TimeSeriesStore."""

    def __init__(self, url: str, org: str, token: str) -> None:
        raise NotImplementedError

    def ensure_buckets(self) -> None:
        """Create hearth_raw / hearth_features / hearth_ml with retention
        policies on first boot (idempotent)."""
        raise NotImplementedError

    # ... TimeSeriesStore methods (write_raw, read_raw, write_features,
    #     read_features, write_prediction, write_label, read_labels,
    #     write_heartbeat) — Phase 1/2.

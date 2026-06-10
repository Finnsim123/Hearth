"""EventSource adapter — Home Assistant WebSocket API (ADR-2).

Subscribes to state_changed for bound entities only; writes nothing itself
(the ingest service composes this with TimeSeriesStore). Reconnect with
exponential backoff; on reconnect, fetch missed states via REST
/api/history/period (gap-fill) so ingest survives restarts on either side.
Connection settings come from AppRepo (configured in the UI Settings page),
tokens decrypted with HEARTH_SECRET.
"""
from __future__ import annotations


class HaWebSocketSource:
    """Implements domain.ports.EventSource."""

    def __init__(self, repo) -> None:  # AppRepo
        raise NotImplementedError

    async def subscribe(self, entity_ids):  # -> AsyncIterator[EntityState]
        raise NotImplementedError

    async def history(self, entity_ids, start, end):  # -> list[EntityState]
        raise NotImplementedError

    async def discover_entities(self) -> list[dict]:
        """All HA entities + attributes — feeds the onboarding suggestion
        heuristics (device_class/domain/unit/name -> proposed Role)."""
        raise NotImplementedError

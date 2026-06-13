"""EventSource adapter — Home Assistant WebSocket API (ADR-2).

Auth -> subscribe state_changed -> yield EntityState for bound entities only.
Reconnects with exponential backoff; the ingest service gap-fills via
history() after each reconnect. Connection settings come from AppRepo
(kind='ha'), token decrypted by security.py inside app_db.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import aiohttp

from ..domain.schemas import EntityState

log = logging.getLogger(__name__)


def _to_state(entity_id: str, payload: dict) -> EntityState:
    ts = payload.get("last_updated") or datetime.now(timezone.utc).isoformat()
    return EntityState(entity_id=entity_id, state=payload.get("state"),
                       attributes=payload.get("attributes", {}),
                       ts=datetime.fromisoformat(ts.replace("Z", "+00:00")))


class HaWebSocketSource:
    """Implements domain.ports.EventSource."""

    def __init__(self, repo) -> None:
        self.repo = repo
        self._id = 0

    def _conn(self) -> dict:
        conn = self.repo.get_connection("ha")
        if conn is None:
            raise RuntimeError("HA connection not configured")
        return conn

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _authed_ws(self, session: aiohttp.ClientSession, conn: dict):
        url = conn["url"].rstrip("/") + "/api/websocket"
        ws = await session.ws_connect(url, heartbeat=30)
        msg = await ws.receive_json()                      # auth_required
        if msg.get("type") == "auth_required":
            await ws.send_json({"type": "auth", "access_token": conn["token"]})
            msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            await ws.close()
            raise RuntimeError("HA auth failed")
        return ws

    async def _command(self, ws, payload: dict) -> dict:
        cid = self._next_id()
        await ws.send_json({**payload, "id": cid})
        while True:
            msg = await ws.receive_json()
            if msg.get("id") == cid and msg.get("type") == "result":
                if not msg.get("success"):
                    raise RuntimeError(f"HA command failed: {msg}")
                return msg.get("result") or {}

    async def subscribe(self, entity_ids: list[str]) -> AsyncIterator[EntityState]:
        """Infinite stream with reconnect/backoff. Caller filters nothing —
        only the requested entity_ids are yielded."""
        from ..domain.health import clear_issue, record_issue
        wanted = set(entity_ids)
        backoff = 1.0
        fails = 0
        while True:
            try:
                conn = self._conn()
                async with aiohttp.ClientSession() as session:
                    ws = await self._authed_ws(session, conn)
                    sub_id = self._next_id()
                    await ws.send_json({"id": sub_id, "type": "subscribe_events",
                                        "event_type": "state_changed"})
                    backoff = 1.0
                    fails = 0
                    clear_issue(self.repo, "ha_unreachable")   # connected → all good
                    log.info("HA WebSocket connected (%d entities watched)", len(wanted))
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        data = msg.json()
                        if data.get("type") != "event":
                            continue
                        ev = data["event"]["data"]
                        eid = ev.get("entity_id")
                        new = ev.get("new_state")
                        if eid in wanted and new:
                            yield _to_state(eid, new)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                fails += 1
                log.warning("HA WS disconnected (%s) — retry in %.0fs", exc, backoff)
                if fails >= 2:          # not a one-off blip → tell the user
                    record_issue(self.repo, "ha_unreachable",
                                 "I can't reach Home Assistant",
                                 "Lost the connection and I'm retrying. Check Home Assistant "
                                 "is running and the URL/token are right.",
                                 cta={"label": "Settings", "href": "/settings"})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def history(self, entity_ids: list[str], start: datetime,
                      end: datetime) -> list[EntityState]:
        """Gap-fill / warm-start via REST /api/history/period.

        The timestamp is in the URL PATH and carries a `+00:00` offset — left raw,
        the `+` decodes to a space and HA returns 400. So the path segment is
        percent-encoded and the rest go through aiohttp's `params` (which encodes
        `+`, commas, etc.). `minimal_response`/`no_attributes` are presence flags;
        HA treats `key=` as present.
        """
        from urllib.parse import quote
        conn = self._conn()
        base = conn["url"].rstrip("/")
        url = f"{base}/api/history/period/{quote(start.isoformat())}"
        params = {"end_time": end.isoformat(), "filter_entity_id": ",".join(entity_ids),
                  "minimal_response": "", "no_attributes": ""}
        headers = {"Authorization": f"Bearer {conn['token']}"}
        out: list[EntityState] = []
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers,
                                    timeout=aiohttp.ClientTimeout(60)) as r:
                r.raise_for_status()
                for entity_series in await r.json():
                    eid = entity_series[0].get("entity_id") if entity_series else None
                    for item in entity_series:
                        out.append(_to_state(item.get("entity_id", eid), item))
        return out

    async def discover_entities(self) -> list[dict]:
        """Inventory metadata: states + entity registry + area names."""
        conn = self._conn()
        async with aiohttp.ClientSession() as session:
            ws = await self._authed_ws(session, conn)
            states = await self._command(ws, {"type": "get_states"})
            try:
                registry = await self._command(ws, {"type": "config/entity_registry/list"})
                areas = await self._command(ws, {"type": "config/area_registry/list"})
            except Exception:
                registry, areas = [], []
            await ws.close()
        area_names = {a["area_id"]: a["name"] for a in areas}
        reg = {r["entity_id"]: r for r in registry}
        out = []
        for st in states:
            eid = st["entity_id"]
            attrs = st.get("attributes", {})
            r = reg.get(eid, {})
            out.append({
                "entity_id": eid,
                "domain": eid.split(".")[0],
                "friendly_name": attrs.get("friendly_name"),
                "device_class": attrs.get("device_class") or r.get("original_device_class"),
                "state_class": attrs.get("state_class"),
                "unit": attrs.get("unit_of_measurement"),
                "area": area_names.get(r.get("area_id")),
                "entity_category": r.get("entity_category"),
                "disabled": bool(r.get("disabled_by")),
                "state": st.get("state"),
            })
        return out

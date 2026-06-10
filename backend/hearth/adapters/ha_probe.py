"""Stateless HA probes — used by the wizard BEFORE any connection is saved.

REST-only (no WebSocket): /api/states gives every entity with attributes,
enough for a live count and a heuristic-bindable inventory. The richer
WS-based discover_entities() (entity/area registry) runs after the connection
is saved; this is the honest pre-save preview.
"""
from __future__ import annotations

import aiohttp


async def probe(url: str, token: str) -> dict:
    """-> {reachable, authed, entities, version, error} — staged truth."""
    out = {"reachable": False, "authed": False, "entities": 0,
           "version": None, "timezone": None, "error": None}
    base = url.rstrip("/")
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/api/config",
                                   headers={"Authorization": f"Bearer {token}"}) as r:
                out["reachable"] = True
                if r.status in (401, 403):
                    out["error"] = "Token rejected"
                    return out
                r.raise_for_status()
                out["authed"] = True
                cfg = await r.json()
                out["version"] = cfg.get("version")
                out["timezone"] = cfg.get("time_zone")
            async with session.get(f"{base}/api/states",
                                   headers={"Authorization": f"Bearer {token}"}) as r:
                r.raise_for_status()
                out["entities"] = len(await r.json())
    except aiohttp.ClientConnectorError as exc:
        out["error"] = f"Can't reach HA at this URL ({exc.__class__.__name__})"
    except Exception as exc:
        out["error"] = str(exc)
    return out


async def rest_inventory(url: str, token: str) -> list[dict]:
    """Inventory metadata from /api/states only (no registry — area unknown)."""
    base = url.rstrip("/")
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base}/api/states",
                               headers={"Authorization": f"Bearer {token}"}) as r:
            r.raise_for_status()
            states = await r.json()
    out = []
    for st in states:
        eid = st["entity_id"]
        attrs = st.get("attributes", {})
        out.append({
            "entity_id": eid,
            "domain": eid.split(".")[0],
            "friendly_name": attrs.get("friendly_name"),
            "device_class": attrs.get("device_class"),
            "unit": attrs.get("unit_of_measurement"),
            "area": None,
            "disabled": False,
            "state": st.get("state"),
        })
    return out

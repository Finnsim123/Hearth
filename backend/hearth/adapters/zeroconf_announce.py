"""mDNS/zeroconf announcer — makes the HA config flow feel magical.

On startup Hearth registers `_hearth._tcp.local.` (port 8420, TXT: version,
uuid). Because the integration's manifest declares this service type, Home
Assistant on the same LAN DISCOVERS Hearth automatically: the user sees
"Discovered: Hearth" (or opens the config flow from the wizard's deep link)
with the host already filled in — only the API token is typed by hand.

Lifecycle: register on app startup, unregister on shutdown, re-register on IP
change (zeroconf lib handles interface churn).
"""
from __future__ import annotations


class ZeroconfAnnouncer:
    def __init__(self, port: int = 8420) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

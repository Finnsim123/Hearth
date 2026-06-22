"""SSRF guard for user-supplied connection URLs (HA / InfluxDB / LLM).

Hearth's whole job is talking to the user's own infra, so loopback and private
(RFC1918) targets are LEGITIMATE on a homelab and must be allowed. The real SSRF
prize is the cloud metadata endpoint and other link-local/reserved ranges — those
are blocked. Host names are resolved so a name that points at a blocked address is
caught too.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def url_block_reason(url: str) -> str | None:
    """Return a human reason the URL is unsafe to fetch, or None if allowed.
    Blocks non-http(s) schemes and any host resolving to a link-local
    (incl. 169.254.169.254 metadata), unspecified, multicast or reserved
    address. Loopback + private are allowed (legitimate homelab targets)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return "URL must start with http:// or https://"
    host = parsed.hostname
    if not host:
        return "URL has no host"
    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except OSError:
        return f"could not resolve host {host!r}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback:
            continue   # localhost is a legitimate homelab target
        # NB: check link-local explicitly — Python's is_private INCLUDES
        # 169.254.0.0/16, so it must not be used as an allow-list here.
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            return f"refusing to connect to {ip} (link-local/metadata/reserved address)"
        # everything else (incl. RFC1918 private) is allowed for homelab use
    return None

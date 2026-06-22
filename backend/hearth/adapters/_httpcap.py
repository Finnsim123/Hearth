"""Size-bounded reads for outbound aiohttp responses.

The HA / LLM endpoints are user-configured, so a hostile or buggy upstream could
return a multi-GB body and OOM the box on an unbounded `r.text()` / `r.json()`.
Read in chunks and abort past a cap instead.
"""
from __future__ import annotations

import json

DEFAULT_MAX = 32 * 1024 * 1024   # 32 MB — generous for a big-home /api/states dump


async def read_capped(resp, max_bytes: int = DEFAULT_MAX) -> bytes:
    """Read the response body, raising ValueError once it exceeds max_bytes."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(65536):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} bytes — refusing to buffer")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_text_capped(resp, max_bytes: int = DEFAULT_MAX) -> str:
    return (await read_capped(resp, max_bytes)).decode("utf-8", errors="replace")


async def read_json_capped(resp, max_bytes: int = DEFAULT_MAX):
    return json.loads(await read_capped(resp, max_bytes))

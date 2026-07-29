"""MCP wrapper vs REST response shapes — the advisories envelope regression.

FastMCP validates each tool's return against its annotation, so a tool that
returns an API envelope dict where it declared list[dict] fails EVERY call.
These tests pin the unwrap. Skipped cleanly where the `mcp` package isn't
installed (it's a desktop-side dependency, not a backend one).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "mcp"))
hearth_mcp = pytest.importorskip("hearth_mcp")


@pytest.mark.asyncio
async def test_advisories_unwraps_envelope(monkeypatch):
    async def fake_get(path, params=None):
        assert path == "/advisories"
        return {"advisories": [{"kind": "binding", "title": "x"}], "events": []}
    monkeypatch.setattr(hearth_mcp, "_get", fake_get)
    out = await hearth_mcp.advisories()
    assert out == [{"kind": "binding", "title": "x"}]


@pytest.mark.asyncio
async def test_advisories_tolerates_bare_list_and_empty(monkeypatch):
    async def bare(path, params=None):
        return [{"kind": "x"}]
    monkeypatch.setattr(hearth_mcp, "_get", bare)
    assert await hearth_mcp.advisories() == [{"kind": "x"}]

    async def empty(path, params=None):
        return {"events": []}                    # envelope without the key
    monkeypatch.setattr(hearth_mcp, "_get", empty)
    assert await hearth_mcp.advisories() == []


@pytest.mark.asyncio
async def test_bare_list_tools_stay_bare(monkeypatch):
    """list_people/pending_questions return their endpoints' bare lists —
    if a future API change wraps those in envelopes, this catches it."""
    async def fake_get(path, params=None):
        return [{"id": "alex"}]
    monkeypatch.setattr(hearth_mcp, "_get", fake_get)
    assert await hearth_mcp.list_people() == [{"id": "alex"}]
    assert await hearth_mcp.pending_questions() == [{"id": "alex"}]

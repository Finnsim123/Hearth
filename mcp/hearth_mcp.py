#!/usr/bin/env python3
"""Hearth MCP server — lets Claude talk to your Hearth instance.

A thin, read-first wrapper over Hearth's REST API, exposed as MCP tools so an MCP
client (Claude Desktop) can ask "what's Alex doing right now?", "which patterns
need naming?", "how's the model doing?" — and take a few safe actions (answer a
question, name a pattern, trigger a train). Destructive ops (forget/delete a
person) are deliberately NOT exposed here — those stay in the Hearth UI.

Transport: stdio (Claude Desktop launches this process). It talks to Hearth over
HTTP with an integration-scoped API token, so nothing about the running Hearth
service changes.

Config (env):
  HEARTH_URL    base URL of your Hearth instance   (default http://localhost:8420)
  HEARTH_TOKEN  an integration-scoped API token    (Settings → mint one)

Run standalone to sanity-check:  HEARTH_TOKEN=... python hearth_mcp.py
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

HEARTH_URL = os.environ.get("HEARTH_URL", "http://localhost:8420").rstrip("/")
HEARTH_TOKEN = os.environ.get("HEARTH_TOKEN", "")

mcp = FastMCP("hearth")


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if HEARTH_TOKEN:
        h["Authorization"] = f"Bearer {HEARTH_TOKEN}"
    return h


async def _get(path: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{HEARTH_URL}/api{path}", params=params or {}, headers=_headers())
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{HEARTH_URL}/api{path}", json=body or {}, headers=_headers())
        r.raise_for_status()
        return r.json()


def _pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


# ── reads ────────────────────────────────────────────────────────────────────
@mcp.tool()
async def list_people() -> list[dict]:
    """List the household members Hearth predicts for (id + name)."""
    return await _get("/persons")


@mcp.tool()
async def current_activity(person: str | None = None) -> list[dict]:
    """What Hearth currently thinks each person is doing: the latest predicted
    activity and its confidence. Pass a person id to scope to one member."""
    data = await _get("/predictions", {"hours": 3, **({"person": person} if person else {})})
    out = []
    for pid, rows in (data.get("persons") or {}).items():
        if not rows:
            out.append({"person": pid, "activity": "unknown", "note": "no recent prediction"})
            continue
        last = rows[-1]
        out.append({"person": pid,
                    "activity": last.get("smoothed") or last.get("predicted") or "unknown",
                    "confidence": _pct(last.get("confidence")),
                    "at": last.get("time")})
    return out


@mcp.tool()
async def model_status(person: str | None = None) -> list[dict]:
    """Per-person model health: real-world accuracy, whether it's validated, and
    how much training data it has. The honest headline, same as the Models page."""
    models = await _get("/models")
    by_person: dict[str, list] = {}
    for m in models:
        by_person.setdefault(m.get("person_id"), []).append(m)
    out = []
    for pid, ms in by_person.items():
        if person and pid != person:
            continue
        live = next((m for m in ms if m.get("promoted")), ms[0] if ms else None)
        mt = (live or {}).get("metrics", {}) or {}
        n_gold = mt.get("n_gold") or 0
        out.append({
            "person": pid,
            "real_world_accuracy": _pct(mt.get("accuracy_gold")) if n_gold >= 30 else f"gathering ({n_gold}/30)",
            "on_tricky_moments": _pct(mt.get("accuracy_confirmed")),
            "status": mt.get("validation_status", "unknown"),
            "train_windows": mt.get("n_train"),
            "versions": len(ms),
        })
    return out


@mcp.tool()
async def capability(person: str | None = None) -> dict:
    """The honest "what I can and can't do": each activity tagged reliable /
    learning / not-working / blind, with a plain-language reason and remedy."""
    q = {"person": person} if person else {}
    rep = await _get("/capability", q)
    return {
        "overall": rep.get("overall"),
        "activities": [{"activity": a.get("name"), "verdict": a.get("tier"),
                        "why": a.get("reason"), "would_help": a.get("remedy")}
                       for a in rep.get("activities", [])],
    }


@mcp.tool()
async def behaviour_summary(person: str | None = None, days: int = 7) -> dict:
    """Routines over the last `days`: the home footprint (rooms/roaming/pacing),
    the daily-rhythm regularity, and sleep/away time. Descriptive, not the model."""
    q = {"days": days, **({"person": person} if person else {})}
    data = await _get("/behaviour", q)
    fp, ry = data.get("footprint") or {}, data.get("rhythm") or {}
    return {
        "footprint": {"rooms_per_spell": fp.get("rooms"),
                      "movement": fp.get("roaming_label"),
                      "pacing": fp.get("pacing_label"),
                      "trend": fp.get("trend")} if fp else None,
        "rhythm": {"regularity": _pct(ry.get("daily_regularity")),
                   "summary": ry.get("regularity_label"),
                   "cycle": ry.get("period_label")} if ry else None,
    }


@mcp.tool()
async def sensor_health() -> dict:
    """A summary of sensor coverage: how many bound sensors are live vs
    constant vs no-data, and any presence gaps for household members."""
    data = await _get("/bindings/health")
    binds = data.get("bindings", [])
    counts: dict[str, int] = {}
    for b in binds:
        counts[b.get("status", "?")] = counts.get(b.get("status", "?"), 0) + 1
    return {"total_sensors": len(binds), "by_status": counts,
            "members": [{"name": m.get("name"), "presence_linked": m.get("has_person"),
                         "presence_seen": m.get("person_alive")}
                        for m in data.get("members", [])]}


@mcp.tool()
async def home_wiring() -> list[dict]:
    """The home's temporal wiring — sensors that reliably fire in sequence
    (e.g. bathroom → bedroom light ~2 min), from lead/lag cross-correlation."""
    data = await _get("/bindings/leadlag")
    return [{"from": e.get("from"), "to": e.get("to"),
             "lead_min": e.get("lag_min"), "strength": _pct(e.get("strength"))}
            for e in data.get("edges", [])]


@mcp.tool()
async def patterns() -> list[dict]:
    """Recurring patterns Hearth discovered but can't name yet — candidates to
    name (which labels weeks of history at once). Returns cluster id + signature."""
    cards = await _get("/clusters")
    out = []
    for c in cards:
        if c.get("status") not in (None, "new"):
            continue
        sig = ", ".join(f"{f}" for f, _ in (c.get("signature") or [])[:4])
        out.append({"cluster_id": c.get("id"), "person": c.get("person_id"),
                    "windows": c.get("n_windows"), "signature": sig,
                    "suggested": c.get("suggested_slug")})
    return out


@mcp.tool()
async def pending_questions(person: str | None = None) -> list[dict]:
    """The open questions Hearth is waiting on (windows it's unsure about).
    Answer one with answer_question(question_id, activity_slug)."""
    q = {"person": person} if person else {}
    return await _get("/inbox", q)


@mcp.tool()
async def advisories() -> list[dict]:
    """Active advisories — things Hearth wants you to know (a demoted sensor, a
    new device to integrate, coverage gaps, out-of-credit, …)."""
    return await _get("/advisories")


# ── safe actions ─────────────────────────────────────────────────────────────
@mcp.tool()
async def answer_question(question_id: int, answer: str) -> dict:
    """Answer a pending question with an activity slug (a confirmed, human label —
    the strongest training signal). Get ids from pending_questions()."""
    return await _post(f"/inbox/{question_id}/answer", {"answer": answer})


@mcp.tool()
async def name_pattern(cluster_id: int, activity_slug: str | None = None,
                       name: str | None = None) -> dict:
    """Name a discovered pattern — pass an existing `activity_slug` OR a new
    `name`. Emits discovered labels for the whole pattern (weeks of history)."""
    body: dict = {}
    if activity_slug:
        body["activity_slug"] = activity_slug
    elif name:
        body["name"] = name
    else:
        return {"error": "pass either activity_slug or name"}
    return await _post(f"/clusters/{cluster_id}/name", body)


@mcp.tool()
async def train_model(person_id: str) -> dict:
    """Kick off a training run for one person. It only goes live if the promotion
    gate says the new model isn't credibly worse than the current one."""
    return await _post("/models/train", {"person_id": person_id})


if __name__ == "__main__":
    mcp.run()

"""Bespoke automation ideas — a one-shot LLM nudge for the onboarding scan result.

Sends only the user's sensor CATEGORY names (not stats, not entity ids), and asks
for a few delightful Home-Assistant automations built on Hearth's per-person
activity sensor. Inspirational copy only — never executable, so a hallucinated
specific is harmless. Returns [] without an LLM, so the static + heuristic ideas
always stand on their own.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_SCHEMA = ('Return ONLY JSON: {"ideas":[{"title":"...","text":"..."}]} — exactly 3 '
           'or 4 ideas. title <= 5 words; text one concrete sentence. No emojis.')


async def suggest_ideas(advisor, categories: list[str]) -> list[dict]:
    """advisor: an OpenRouterAdvisor (its _chat returns parsed JSON). categories:
    triage category keys present in the home (e.g. ['presence','lights','media'])."""
    cats = [c for c in (categories or []) if isinstance(c, str)][:20]
    if advisor is None or not cats:
        return []
    system = ("You suggest calm, concrete Home Assistant automations a household could "
              "build once Hearth publishes a per-person activity sensor "
              "(sensor.hearth_<person>_activity — states like cooking, asleep, away, "
              "movie, working) plus a hearth_activity_changed event. Practical and "
              "warm, never creepy. " + _SCHEMA)
    user = ("The home has these sensor categories: " + ", ".join(cats) +
            ". Suggest automations that pair the activity sensor with those sensors.")
    try:
        out = await advisor._chat(system, user, max_tokens=500, task="ideas")
    except Exception as exc:
        log.warning("idea suggestion failed: %s", exc)
        return []
    items = out.get("ideas") if isinstance(out, dict) else (out if isinstance(out, list) else None)
    if not isinstance(items, list):
        return []
    clean: list[dict] = []
    for it in items[:4]:
        if isinstance(it, dict) and it.get("title") and it.get("text"):
            clean.append({"title": str(it["title"]).strip()[:60],
                          "text": str(it["text"]).strip()[:180]})
    return clean

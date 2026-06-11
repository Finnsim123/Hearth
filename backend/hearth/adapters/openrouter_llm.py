"""LlmAdvisor adapter — OpenRouter / any OpenAI-compatible chat endpoint.

THE BRAIN for the household-specific half (ADR-12): mapping arbitrary entity
names ("matras_links", "Bett-Sensor", "fred_links") to roles, and writing
rules a template can't know ("kaffsch_sign on = espresso warming = wake soon").

Constraints, enforced here:
- output is STRICTLY validated data — unknown roles, malformed ASTs, alien
  feature names are dropped item-by-item; a garbage response degrades to the
  heuristic floor, never to a crash
- prompts contain entity METADATA + aggregate stats only, never raw history
- every call is logged with token usage for the Settings cost log
"""
from __future__ import annotations

import json
import logging
import re

import aiohttp

from ..domain.features.registry import all_recipes
from ..domain.schemas import Activity, Binding, ClusterCard, Role, Rule

log = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-4o-mini"
_SLUG = re.compile(r"^[a-z0-9_]{1,60}$")
_OPS = {">", "<", ">=", "<=", "==", "!="}
TEMPORAL_FEATS = {"hour_of_day", "day_of_week", "is_weekend"}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def _extract_json(text: str):
    """Parse model output: tolerate code fences and leading prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def validate_predicate(node, allowed_feats: set[str]) -> bool:
    """Whitelist walker for the rule AST — the safety boundary."""
    if not isinstance(node, dict):
        return False
    if "all" in node or "any" in node:
        children = node.get("all") or node.get("any")
        return (isinstance(children, list) and len(children) >= 1
                and all(validate_predicate(c, allowed_feats) for c in children))
    if "not" in node:
        return validate_predicate(node["not"], allowed_feats)
    if {"feat", "op", "value"} <= set(node):
        return (node["op"] in _OPS
                and isinstance(node["value"], (int, float))
                and isinstance(node["feat"], str)
                and node["feat"] in allowed_feats)
    return False


def allowed_features(bindings: list[Binding]) -> set[str]:
    feats = set(TEMPORAL_FEATS)
    recipes = all_recipes()
    for b in bindings:
        for suffix in recipes[b.role].suffixes:
            feats.add(f"{b.name}_{suffix}")
    return feats


class OpenRouterAdvisor:
    """Implements domain.ports.LlmAdvisor."""

    def __init__(self, repo) -> None:
        self.repo = repo

    async def _chat(self, system: str, user: str, max_tokens: int = 4000):
        conn = self.repo.get_connection("llm")
        if conn is None:
            raise RuntimeError("LLM connection not configured")
        model = (conn.get("options") or {}).get("model") or DEFAULT_MODEL
        if model == "auto":
            model = DEFAULT_MODEL
        url = f"{conn['url'].rstrip('/')}/chat/completions"
        payload = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                    headers={"Authorization": f"Bearer {conn['token']}"},
                                    timeout=aiohttp.ClientTimeout(120)) as r:
                r.raise_for_status()
                data = await r.json()
        usage = data.get("usage", {})
        finish = data["choices"][0].get("finish_reason")
        log.info("LLM call: %s in=%s out=%s finish=%s", model,
                 usage.get("prompt_tokens"), usage.get("completion_tokens"), finish)
        if finish == "length":
            log.warning("LLM response TRUNCATED at max_tokens — items may be lost")
        return _extract_json(data["choices"][0]["message"]["content"])

    # ── bindings: the name->role brain ──────────────────────────────────────
    async def propose_bindings(self, inventory: list[dict],
                               persons: list | None = None) -> list[Binding]:
        roles = ", ".join(r.value for r in Role)
        member_ids = [p.id for p in (persons or [])]
        lines = [f"{e['entity_id']} | {e.get('device_class') or '-'} | "
                 f"{e.get('unit') or '-'} | {e.get('friendly_name') or '-'}"
                 for e in inventory if not e.get("disabled")]
        system = (
            "You map Home Assistant entities to semantic roles for a home "
            "activity-recognition system. Names may be in ANY language or be "
            "nicknames — infer meaning (e.g. 'matras'=mattress=bed, "
            "'vermogen'=power, 'wekker'=alarm clock). Be selective: only map "
            "entities genuinely useful for knowing what PEOPLE are doing at "
            "home. Skip diagnostics, infrastructure, weather, forecasts. "
            "Network nuance: skip GENERIC device trackers (laptops, cameras, "
            "IoT), BUT a household member's PHONE tracker (role person, set "
            "person) and router/network occupancy signals — connected-device "
            "count, total throughput — ARE useful presence proxies; include "
            "them (role person for a phone, else custom).\n"
            f"Valid roles: {roles}.\n"
            f"Household members: {member_ids or 'unknown'}. PERSONAL devices "
            "(alarm clock, phone focus/steps/battery, wearables) must carry "
            "\"person\": the owning member id when the entity name implies an "
            "owner — wrong-person signals poison that person's model.\n"
            "Reply with ONLY a JSON array: [{\"entity_id\": str, \"role\": str, "
            "\"name\": short_snake_case_slug, \"room\": str|null, "
            "\"person\": member_id|null, "
            "\"reason\": str}] — nothing else. Keep each reason under 8 words; "
            "omit the reason field entirely when the mapping is obvious.")
        out: list[Binding] = []
        seen: set[str] = set()
        for i in range(0, len(lines), 300):           # chunk large homes
            try:
                items = await self._chat(system, "\n".join(lines[i:i + 300]),
                                         max_tokens=8000)
            except Exception as exc:
                log.warning("propose_bindings chunk failed: %s", exc)
                continue
            if not isinstance(items, list):
                continue
            valid_ids = {e["entity_id"] for e in inventory}
            from ..domain.onboarding.advisor import is_bindable
            for it in items:
                try:
                    role = Role(it["role"])
                    name = _slugify(it.get("name") or it["entity_id"].split(".")[-1])
                    person = it.get("person")
                    person = person if person in member_ids else None
                    reason = str(it.get("reason", "")).strip()
                    standard = is_bindable(it["entity_id"], role)
                    appealed = (not standard and bool(reason)
                                and is_bindable(it["entity_id"], role, override=True))
                    if (it["entity_id"] in valid_ids and _SLUG.match(name)
                            and it["entity_id"] not in seen
                            and (standard or appealed)):
                        seen.add(it["entity_id"])   # dedupe on entity, not name
                        # feature prefixes must stay unique — disambiguate a
                        # name collision instead of dropping a real sensor
                        base, n = name, 2
                        used = {b.name for b in out}
                        while name in used:
                            name, n = f"{base}_{n}", n + 1
                        opts = {"llm_override": reason} if appealed else {}
                        out.append(Binding(entity_id=it["entity_id"], role=role,
                                           name=name, room=it.get("room"),
                                           person_id=person, options=opts))
                except (KeyError, ValueError):
                    continue
        return out

    # ── taxonomy ─────────────────────────────────────────────────────────────
    async def propose_taxonomy(self, inventory: list[dict]) -> list[Activity]:
        domains = sorted({e["entity_id"].split(".")[0] for e in inventory})
        system = (
            "Given a smart home's entity domains, propose 4-8 daily activities "
            "an activity-recognition system should learn. Always include "
            "sleeping, away, home. Reply ONLY JSON: [{\"slug\": snake_case, "
            "\"name\": str, \"phrase\": verb_phrase_for_notifications}]")
        try:
            items = await self._chat(system, f"domains: {', '.join(domains)}")
        except Exception as exc:
            log.warning("propose_taxonomy failed: %s", exc)
            return []
        out = []
        for it in items if isinstance(items, list) else []:
            slug = _slugify(str(it.get("slug", "")))
            if _SLUG.match(slug):
                from ..domain.labeling.active import _is_sleep_like
                out.append(Activity(silent=_is_sleep_like(slug),
                                    slug=slug, name=str(it.get("name", slug)).strip()[:40],
                                    phrase=str(it.get("phrase", "")).strip()[:60] or None))
        return out

    # ── rules: household-specific labeling logic ────────────────────────────
    async def propose_rules(self, bindings: list[Binding],
                            activities: list[Activity]) -> list[Rule]:
        feats = allowed_features(bindings)
        binding_desc = "\n".join(
            f"- {b.name} (role={b.role.value}, room={b.room or '?'}"
            f"{', person=' + b.person_id if b.person_id else ''})"
            for b in bindings)
        act_desc = ", ".join(a.slug for a in activities)
        system = (
            "You write labeling rules for a home activity-recognition system. "
            "A rule is a JSON predicate over FEATURE columns mapped to an "
            "activity. Grammar: {\"all\":[...]}/{\"any\":[...]}/{\"not\":...}/"
            "{\"feat\":str,\"op\":one of > < >= <= == !=,\"value\":number}. "
            "USE ONLY features from the provided list. hour_of_day is LOCAL "
            "0-23. Write high-PRECISION rules (better to not fire than to "
            "mislabel). Exploit household-specific signals a generic template "
            "would miss. Reply ONLY JSON: [{\"activity\": slug, \"person\": "
            "str|null, \"priority\": int 10-90 (lower wins), \"predicate\": "
            "object, \"reason\": str}]")
        user = (f"Activities: {act_desc}\n\nBindings:\n{binding_desc}\n\n"
                f"Allowed features:\n{', '.join(sorted(feats))}")
        try:
            items = await self._chat(system, user)
        except Exception as exc:
            log.warning("propose_rules failed: %s", exc)
            return []
        slugs = {a.slug for a in activities}
        persons = {b.person_id for b in bindings if b.person_id} | {None}
        out = []
        for it in items if isinstance(items, list) else []:
            try:
                if (it["activity"] in slugs
                        and it.get("person") in persons
                        and validate_predicate(it["predicate"], feats)):
                    out.append(Rule(activity_slug=it["activity"],
                                    person_id=it.get("person"),
                                    priority=int(min(max(it.get("priority", 60), 10), 90)),
                                    predicate=it["predicate"]))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    # ── weak annotation + cluster naming ────────────────────────────────────
    async def annotate_windows(self, window_summaries: list[dict],
                               activities: list[Activity]) -> list[tuple[str | None, float]]:
        slugs = [a.slug for a in activities]
        system = (
            f"Label each window summary with one of: {', '.join(slugs)} or null "
            "if unclear. Reply ONLY JSON: [{\"i\": int, \"label\": str|null, "
            "\"confidence\": 0..1}] in input order.")
        results: list[tuple[str | None, float]] = [(None, 0.0)] * len(window_summaries)
        for i in range(0, len(window_summaries), 200):
            chunk = window_summaries[i:i + 200]
            user = "\n".join(f"{i + j}: {json.dumps(w)}" for j, w in enumerate(chunk))
            try:
                items = await self._chat(system, user)
            except Exception as exc:
                log.warning("annotate chunk failed: %s", exc)
                continue
            for it in items if isinstance(items, list) else []:
                try:
                    idx = int(it["i"])
                    label = it.get("label")
                    conf = float(it.get("confidence", 0))
                    if 0 <= idx < len(results) and (label is None or label in slugs):
                        results[idx] = (label, conf)
                except (KeyError, TypeError, ValueError):
                    continue
        return results

    async def suggest_cluster_name(self, card: ClusterCard,
                                   activities: list[Activity]) -> str | None:
        system = ("Given a cluster signature from home sensor data, suggest "
                  "which activity it is. Reply ONLY JSON: {\"slug\": str|null}")
        try:
            res = await self._chat(system, json.dumps({
                "signature": card.signature, "hour_histogram": card.hour_histogram}))
            slug = res.get("slug") if isinstance(res, dict) else None
            return slug if slug in {a.slug for a in activities} else None
        except Exception:
            return None

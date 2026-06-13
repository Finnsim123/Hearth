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
from datetime import datetime, timezone

import aiohttp

from ..domain.features.registry import all_recipes
from ..domain.schemas import Activity, Binding, ClusterCard, Role, Rule

log = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-4o-mini"
_SLUG = re.compile(r"^[a-z0-9_]{1,60}$")
_OPS = {">", "<", ">=", "<=", "==", "!="}
TEMPORAL_FEATS = {"hour_of_day", "day_of_week", "is_weekend"}


def choose_model(configured: str | None, fallback: str) -> str:
    """The user's explicitly configured model wins; otherwise (unset or 'auto')
    use the per-task fallback. Lets the architect task default to a stronger
    model without overriding a deliberate user choice."""
    if configured and configured != "auto":
        return configured
    return fallback or DEFAULT_MODEL


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def _preview(text: str, limit: int = 280) -> str:
    """A short, safe snippet of a prompt or reply for the live Welcome transcript."""
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit] + "…"


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
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        # salvage a TRUNCATED top-level array (max_tokens cut it mid-object):
        # keep everything up to the last complete object and close the array.
        if "[" in text:
            arr = text.index("[")
            last = text.rfind("}")
            if last > arr:
                try:
                    return json.loads(text[arr:last + 1] + "]")
                except json.JSONDecodeError:
                    pass
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

    def _prompt(self, key: str, **tokens: str) -> str:
        """Resolve an editable system prompt (override-or-default) from the
        central registry, injecting any [[TOKEN]] values. See domain/prompts.py."""
        from ..domain.prompts import system_prompt
        return system_prompt(self.repo, key, **tokens)

    def _household_activities(self) -> str:
        """Human-readable list of the activities Hearth predicts for this home,
        so the triage/mapping prompts can keep an otherwise-noisy machine sensor
        when an activity is actually ABOUT that machine (e.g. 'crafting' makes a
        3D printer a primary signal). Empty string when unknown (e.g. the wizard
        preview, before a taxonomy exists) — the prompt then omits the clause."""
        get = getattr(self.repo, "activities", None)
        if not callable(get):
            return ""
        try:
            names = [a.name for a in get() if getattr(a, "enabled", True)]
        except Exception:
            return ""
        return ", ".join(dict.fromkeys(n for n in names if n))

    def _set_status(self, ok: bool, code: int, detail: str | None) -> None:
        """Record the health of the last LLM call so the UI (ember buddy,
        Settings) can tell the user when their key is rate-limited / out of
        credit — otherwise the failure is silent and mapping quietly degrades."""
        try:
            self.repo.set_setting("llm.status", {
                "ok": ok, "code": code, "detail": detail,
                "at": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass

    def _set_activity(self, phase: str, task: str | None, **extra) -> None:
        """Narrate the current LLM call for the live Welcome screen: what we're
        sending and what we got back. A single last-event setting (llm.activity);
        no-op when the caller didn't label the task."""
        if not task:
            return
        try:
            self.repo.set_setting("llm.activity", {
                "phase": phase, "task": task,
                "at": datetime.now(timezone.utc).isoformat(), **extra})
        except Exception:
            pass

    def _add_usage(self, model: str, in_tok, out_tok) -> None:
        """Accumulate token usage + a rough running cost for the Settings usage
        counter. Best-effort; a recording failure must never break a call."""
        try:
            from ..domain.onboarding.feature_architect import _price_for
            cur = self.repo.get_setting("llm.usage") or {}
            pin, pout = _price_for(model, self.repo)
            it, ot = int(in_tok or 0), int(out_tok or 0)
            now = datetime.now(timezone.utc).isoformat()
            self.repo.set_setting("llm.usage", {
                "calls": int(cur.get("calls", 0)) + 1,
                "input_tokens": int(cur.get("input_tokens", 0)) + it,
                "output_tokens": int(cur.get("output_tokens", 0)) + ot,
                "est_usd": round(float(cur.get("est_usd", 0.0))
                                 + it / 1e6 * pin + ot / 1e6 * pout, 6),
                "since": cur.get("since") or now,
                "last_at": now})
        except Exception:
            pass

    async def _chat(self, system: str, user: str, max_tokens: int = 4000,
                    model: str | None = None, task: str | None = None,
                    sent: str | None = None):
        conn = self.repo.get_connection("llm")
        if conn is None:
            raise RuntimeError("LLM connection not configured")
        # user's explicit model wins; else the per-task fallback (model arg) or default
        model = choose_model((conn.get("options") or {}).get("model"),
                             model or DEFAULT_MODEL)
        self._set_activity("sending", task, model=model, sent=sent, prompt=_preview(user))
        url = f"{conn['url'].rstrip('/')}/chat/completions"
        payload = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers={"Authorization": f"Bearer {conn['token']}"},
                                        timeout=aiohttp.ClientTimeout(total=120)) as r:
                    text = await r.text()
                    if r.status >= 400:
                        self._set_status(False, r.status, text[:200])
                        self._set_activity("error", task, model=model)
                        raise RuntimeError(f"LLM HTTP {r.status}: {text[:120]}")
                    data = json.loads(text)
        except aiohttp.ClientError as exc:
            self._set_status(False, 0, str(exc)[:160])
            self._set_activity("error", task, model=model)
            raise
        self._set_status(True, 200, None)
        usage = data.get("usage", {})
        finish = data["choices"][0].get("finish_reason")
        log.info("LLM call: %s in=%s out=%s finish=%s", model,
                 usage.get("prompt_tokens"), usage.get("completion_tokens"), finish)
        self._add_usage(model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
        if finish == "length":
            log.warning("LLM response TRUNCATED at max_tokens — items may be lost")
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        items = len(parsed) if isinstance(parsed, (list, dict)) else None
        self._set_activity("received", task, model=model, items=items,
                           out_tokens=usage.get("completion_tokens"), reply=_preview(content))
        return parsed

    # ── coarse triage: cluster the FULL entity list, ids + names only ────────
    async def cluster_entities(self, inventory: list[dict]) -> list[dict]:
        """Stage 0 of the funnel. Sends only entity_id + friendly_name (cheap,
        low-noise) for EVERY entity, asks the model to assign each to a fixed
        functional CATEGORY and judge relevance. Returns raw per-chunk clusters
        ({category, relevant, why, entities}); triage.canonicalize merges them
        into one tidy bucket per category. Degrades to []."""
        items = [e for e in inventory if not e.get("disabled")]
        valid = {e["entity_id"] for e in items}
        lines = [f'{e["entity_id"]} | {e.get("friendly_name") or "-"}' for e in items]
        system = self._prompt("triage_cluster",
                              activities=self._household_activities() or "none set yet")
        out: list[dict] = []
        for i in range(0, len(lines), 400):                 # ids are cheap; big batches
            chunk = lines[i:i + 400]
            try:
                res = await self._chat(system, "\n".join(chunk), max_tokens=8000,
                                       task="Clustering your entities",
                                       sent=f"{len(chunk)} entities")
            except Exception as exc:
                log.warning("cluster_entities chunk failed: %s", exc)
                continue
            for c in res if isinstance(res, list) else []:
                try:
                    ents = [e for e in c.get("entities", []) if e in valid]
                    if not ents:
                        continue
                    # accept either {category} (new) or {label} (older edits)
                    out.append({"category": c.get("category") or c.get("label") or "",
                                "label": str(c.get("label") or c.get("category") or "")[:48],
                                "relevant": bool(c.get("relevant")),
                                "why": str(c.get("why") or "").strip()[:80],
                                "entities": ents})
                except (KeyError, TypeError):
                    continue
        return out

    # ── bindings: the name->role brain ──────────────────────────────────────
    async def propose_bindings(self, inventory: list[dict],
                               persons: list | None = None) -> list[Binding]:
        roles = ", ".join(r.value for r in Role)
        member_ids = [p.id for p in (persons or [])]
        lines = [f"{e['entity_id']} | {e.get('device_class') or '-'} | "
                 f"{e.get('unit') or '-'} | {e.get('friendly_name') or '-'}"
                 for e in inventory if not e.get("disabled")]
        system = self._prompt("map_bindings", roles=roles,
                              members=str(member_ids or "unknown"),
                              activities=self._household_activities() or "unknown")
        out: list[Binding] = []
        seen: set[str] = set()
        for i in range(0, len(lines), 300):           # chunk large homes
            try:
                chunk = lines[i:i + 300]
                items = await self._chat(system, "\n".join(chunk),
                                         max_tokens=8000,
                                         task="Mapping sensors to roles",
                                         sent=f"{len(chunk)} entities")
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

    # ── person ↔ home/away entity matching ───────────────────────────────────
    async def match_person_entities(self, members: list, inventory: list[dict]) -> dict[str, str]:
        """Match each household member to their Home Assistant home/away entity
        (person.* preferred, device_tracker.* fallback). Names may be nicknames
        or in any language — this is exactly the messy-name → structure job the
        LLM is for. Returns {member_id: entity_id}; unknowns degrade to {}."""
        from ..domain.onboarding.advisor import is_person_tracker
        cands = [e for e in inventory
                 if is_person_tracker(e["entity_id"], e.get("friendly_name") or "")
                 and not e.get("disabled")]
        if not members or not cands:
            return {}
        system = self._prompt("match_person")
        user = json.dumps({
            "members": [{"id": p.id, "name": p.name} for p in members],
            "candidates": [{"entity_id": e["entity_id"],
                            "name": e.get("friendly_name") or ""} for e in cands]})
        try:
            res = await self._chat(system, user, max_tokens=1500,
                                   task="Matching people to their trackers",
                                   sent=f"{len(members)} member{'s' if len(members) != 1 else ''}")
        except Exception as exc:
            log.warning("match_person_entities failed: %s", exc)
            return {}
        valid = {e["entity_id"] for e in cands}
        member_ids = {p.id for p in members}
        if not isinstance(res, dict):
            return {}
        return {str(k): str(v) for k, v in res.items()
                if k in member_ids and v in valid}

    # ── room reconciliation ──────────────────────────────────────────────────
    async def propose_room_canon(self, rooms: list[str]) -> dict[str, str]:
        """Map messy room names to a merged canonical set — folding SEMANTIC
        duplicates a string compare misses (Sleepingroom→Bedroom, Backoffice→
        Office). Returns {original: canonical}; unknown/garbage degrades to {}."""
        if len(rooms) < 2:
            return {}
        system = self._prompt("room_canon")
        try:
            res = await self._chat(system, json.dumps(rooms), max_tokens=2000,
                                   task="Tidying room names",
                                   sent=f"{len(rooms)} rooms")
        except Exception as exc:
            log.warning("propose_room_canon failed: %s", exc)
            return {}
        if not isinstance(res, dict):
            return {}
        return {str(k): str(v).strip()[:40] for k, v in res.items()
                if isinstance(v, str) and v.strip()}

    # ── taxonomy ─────────────────────────────────────────────────────────────
    async def propose_taxonomy(self, inventory: list[dict]) -> list[Activity]:
        domains = sorted({e["entity_id"].split(".")[0] for e in inventory})
        system = self._prompt("propose_taxonomy")
        try:
            items = await self._chat(system, f"domains: {', '.join(domains)}",
                                     task="Proposing activities to recognise")
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
        system = self._prompt("propose_rules")
        user = (f"Activities: {act_desc}\n\nBindings:\n{binding_desc}\n\n"
                f"Allowed features:\n{', '.join(sorted(feats))}")
        try:
            items = await self._chat(system, user, max_tokens=8000,
                                     task="Writing labeling rules",
                                     sent=f"{len(bindings)} sensors")
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
        system = self._prompt("annotate_windows", activities=", ".join(slugs))
        results: list[tuple[str | None, float]] = [(None, 0.0)] * len(window_summaries)
        for i in range(0, len(window_summaries), 200):
            chunk = window_summaries[i:i + 200]
            user = "\n".join(f"{i + j}: {json.dumps(w)}" for j, w in enumerate(chunk))
            try:
                items = await self._chat(system, user,
                                         task="Labeling history windows",
                                         sent=f"{len(chunk)} windows")
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

    async def suggest_cluster_names(self, card: ClusterCard, evidence: dict,
                                    activities: list[Activity]) -> list[dict]:
        """2–3 candidate names from the (metadata-only) evidence card. Each:
        {name, slug|None, rationale, confidence, kind}. kind is derived here so
        the UI can route a tap: 'existing' (slug matches an activity) vs 'new'.
        Returns [] on any failure — naming still works by hand."""
        slugs = {a.slug for a in activities}
        names = ", ".join(a.name for a in activities) or "none yet"
        system = self._prompt("name_cluster", ACTIVITIES=names)
        # send only the human-readable, aggregate evidence — no raw series
        ev = {
            "summary": evidence.get("summary"),
            "when": evidence.get("when"),
            "where": evidence.get("where"),
            "weekday_cadence": (evidence.get("cadence") or {}).get("phrase"),
            "defining_signals": [{"signal": p["label"], "direction": p["dir"]}
                                 for p in evidence.get("plain", [])],
            "comes_after": (evidence.get("adjacency") or {}).get("before"),
            "leads_into": (evidence.get("adjacency") or {}).get("after"),
            "resembles": (evidence.get("contrast") or {}).get("name"),
        }
        try:
            res = await self._chat(system, json.dumps(ev), max_tokens=700,
                                   task="Suggesting a name for a pattern")
        except Exception:
            return []
        raw = res.get("suggestions") if isinstance(res, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for it in raw[:3]:
            if not isinstance(it, dict) or not str(it.get("name", "")).strip():
                continue
            slug = it.get("slug")
            slug = slug if isinstance(slug, str) and slug in slugs else None
            try:
                conf = max(0.0, min(1.0, float(it.get("confidence", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            out.append({"name": str(it["name"]).strip()[:40],
                        "slug": slug,
                        "rationale": str(it.get("rationale", "")).strip()[:140],
                        "confidence": round(conf, 2),
                        "kind": "existing" if slug else "new"})
        return out

    # ── feature architect: entity catalog -> validated FeatureSpec (Phase 3) ─
    async def propose_feature_spec(self, catalog: list[dict], activities: list,
                                   mode: str = "conservative"):
        """Orchestrate the three architect passes (selection, per-entity
        features, composites), then validate. Each pass degrades on failure to
        what succeeded; the result is always a validated, executable spec."""
        from ..domain.features.validate import validate_spec
        from ..domain.onboarding.feature_architect import (
            ARCHITECT_MODEL_DEFAULT, assemble_spec, composite_prompt,
            feature_prompt, parse_features, parse_selections, selection_prompt)
        from ..domain.schemas import InfoTier

        arch = self._prompt("feature_architect")   # editable persona (Settings)
        try:
            member_ids = [p.id for p in self.repo.persons()]
        except Exception:
            member_ids = []

        selections = []
        for i in range(0, len(catalog), 150):                 # batch large homes
            batch = catalog[i:i + 150]
            try:
                raw = await self._chat(arch,
                                       selection_prompt(batch, activities, member_ids),
                                       max_tokens=8000, model=ARCHITECT_MODEL_DEFAULT,
                                       task="Choosing useful sensors",
                                       sent=f"{len(batch)} entities")
                selections.extend(parse_selections(raw, catalog=batch,
                                                   member_ids=member_ids))
            except Exception as exc:
                log.warning("feature_spec selection chunk failed: %s", exc)

        kept = [s for s in selections if s.keep and s.reliability != "unusable"
                and s.info_tier not in (None, InfoTier.LOW_INFORMATION)]

        features = []
        if kept:
            try:
                features = parse_features(await self._chat(
                    arch, feature_prompt(kept, mode), model=ARCHITECT_MODEL_DEFAULT,
                    task="Designing features", sent=f"{len(kept)} sensors"))
            except Exception as exc:
                log.warning("feature_spec feature pass failed: %s", exc)
        if kept and features:
            try:
                names = [f.name for f in features]
                features += parse_features(await self._chat(
                    arch, composite_prompt(kept, names, mode),
                    model=ARCHITECT_MODEL_DEFAULT,
                    task="Combining features", sent=f"{len(names)} features"))
            except Exception as exc:
                log.warning("feature_spec composite pass failed: %s", exc)

        conn = self.repo.get_connection("llm") or {}
        model = (conn.get("options") or {}).get("model")
        spec = assemble_spec(selections, features, llm_model=model)
        clean, rejected = validate_spec(spec, mode=mode)
        if rejected:
            log.info("feature_spec: %d features rejected by validation: %s",
                     len(rejected), [n for n, _ in rejected][:8])
        return clean

    # ── maintenance: revise the spec from model feedback (Phase 4) ──────────
    async def revise_feature_spec(self, spec, feedback: dict,
                                  mode: str = "conservative"):
        """One revision round: ask for a minimal add/drop delta targeting the
        confused pairs, apply it, and re-validate. On LLM failure the spec is
        returned unchanged."""
        from ..domain.features.validate import validate_spec
        from ..domain.onboarding.feature_architect import (
            ARCHITECT_MODEL_DEFAULT, parse_delta, revision_prompt)
        try:
            raw = await self._chat(self._prompt("feature_architect"),
                                   revision_prompt(feedback, mode),
                                   model=ARCHITECT_MODEL_DEFAULT)
        except Exception as exc:
            log.warning("revise_feature_spec failed: %s", exc)
            return spec
        add, drop = parse_delta(raw)
        drop_set = set(drop)
        features = [f for f in spec.features if f.name not in drop_set] + add
        revised = spec.model_copy(update={"features": features})
        clean, rejected = validate_spec(revised, mode=mode)
        if rejected:
            log.info("revise_feature_spec: %d features rejected: %s",
                     len(rejected), [n for n, _ in rejected][:8])
        return clean

"""Entity triage — the coarse first pass of the two-stage funnel.

A home can have 1700+ entities; sending all of them WITH metadata + stats to the
LLM is wasteful and noisy. So first we cluster the full list from ids + friendly
names alone (cheap) and let the model decide which clusters matter for activity
prediction. The relevant clusters become the SHORTLIST that the expensive
metadata pass (propose_bindings / feature spec) then analyses.

Clusters are canonicalised into a FIXED set of functional CATEGORIES (presence,
sleep, lights, media, … appliances, network, diagnostics) — so however the LLM
(or the heuristic fallback) splits things, the user sees one tidy bucket per
category with a stable icon, instead of three near-duplicate 'presence' bubbles.
Each category has a default relevance; the LLM may still flip an off-by-default
one ON when a household activity is about it (e.g. a 3D printer for 'crafting').

LLM-driven, with a thin safety floor: a genuine home/away tracker (the single
biggest early-accuracy lever, and few in number) is never dropped. Without an LLM
key we fall back to categorising by heuristic role.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..schemas import Role
from .advisor import is_person_tracker, suggest_role

log = logging.getLogger(__name__)

# Canonical categories: key -> (display label, default relevance, icon name the
# frontend maps to). The icon names match frontend/src/icons.tsx. Default
# relevance encodes "does this kind of thing reflect what PEOPLE are doing".
CATEGORIES: dict[str, tuple[str, bool, str]] = {
    "presence":    ("Presence & motion", True,  "presence"),
    "sleep":       ("Bed & sleep",        True,  "sleeping"),
    "lights":      ("Lights",             True,  "light"),
    "media":       ("Media & TV",         True,  "movie"),
    "doors":       ("Doors & windows",    True,  "door"),
    "climate":     ("Climate",            True,  "env"),
    "power":       ("Plugs & power",      True,  "power"),
    "phone":       ("Phones & wearables", True,  "user"),
    "appliance":   ("Appliances & machines", False, "models"),
    "network":     ("Network & infra",    False, "flow"),
    "diagnostics": ("Diagnostics & signal", False, "monitor"),
    "weather":     ("Weather & forecasts", False, "sun"),
    "other":       ("Other",              False, "more"),
}
_CATEGORY_KEYS = set(CATEGORIES)

# Role -> category, for the no-LLM fallback and as a per-entity safety net when
# the LLM omits/garbles a cluster's category.
_ROLE_CATEGORY: dict[Role, str] = {
    Role.PRESENCE: "presence", Role.PERSON: "presence",
    Role.BED: "sleep", Role.LIGHT: "lights", Role.MEDIA: "media",
    Role.DOOR: "doors", Role.ENV: "climate", Role.POWER: "power",
    Role.FOCUS: "phone", Role.STEPS: "phone", Role.ALARM_TIME: "phone",
    Role.BATTERY: "diagnostics", Role.CUSTOM: "other",
}

# Common LLM category synonyms folded onto the canonical keys.
_CATEGORY_ALIASES: dict[str, str] = {
    "motion": "presence", "occupancy": "presence", "people": "presence",
    "person": "presence", "presence/people": "presence", "presence & motion": "presence",
    "bed": "sleep", "sleep": "sleep", "bedroom": "sleep",
    "light": "lights", "lighting": "lights",
    "tv": "media", "audio": "media", "speaker": "media", "media & tv": "media",
    "door": "doors", "window": "doors", "windows": "doors", "lock": "doors", "contact": "doors",
    "temperature": "climate", "humidity": "climate", "air": "climate", "co2": "climate",
    "thermostat": "climate", "hvac": "climate",
    "plug": "power", "energy": "power", "outlet": "power", "switch": "power",
    "phone": "phone", "wearable": "phone", "fitness": "phone", "steps": "phone",
    "health": "phone", "body": "phone", "watch": "phone", "phones & wearables": "phone",
    "appliance": "appliance", "machine": "appliance", "printer": "appliance",
    "3d printer": "appliance", "kitchen appliance": "appliance",
    "network": "network", "networking": "network", "router": "network", "wifi": "network",
    "wlan": "network", "server": "network", "infrastructure": "network", "infra": "network",
    "diagnostic": "diagnostics", "diagnostics": "diagnostics", "signal": "diagnostics",
    "battery": "diagnostics", "firmware": "diagnostics", "system": "diagnostics",
    "weather": "weather", "forecast": "weather", "sun": "weather",
}


def normalize_category(value) -> str:
    """Fold an LLM-supplied label/category onto a canonical category key."""
    if not value:
        return "other"
    s = str(value).strip().lower()
    if s in _CATEGORY_KEYS:
        return s
    if s in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[s]
    for token, cat in _CATEGORY_ALIASES.items():       # substring match on words
        if token in s:
            return cat
    return "other"


def _role_category(entity: dict) -> str:
    """Deterministic category from heuristic role — the per-entity fallback."""
    role = suggest_role(entity)
    return _ROLE_CATEGORY.get(role, "other") if role else "other"


def _heuristic_clusters(usable: list[dict]) -> list[dict]:
    """No-LLM fallback: one raw cluster per heuristic-role category."""
    groups: dict[str, dict] = {}
    for e in usable:
        cat = _role_category(e)
        g = groups.setdefault(cat, {"category": cat, "relevant": CATEGORIES[cat][1],
                                    "why": "", "entities": []})
        g["entities"].append(e["entity_id"])
    return list(groups.values())


def _canonicalize(raw_clusters: list[dict], usable: list[dict]) -> list[dict]:
    """Merge however the LLM/heuristic split things into ONE bucket per canonical
    category. Category per entity = its cluster's category, falling back to the
    entity's heuristic role when the cluster's category is missing/garbled.
    A bucket is relevant if the category defaults relevant OR any contributing
    cluster marked it relevant (lets 'crafting' opt a 3D printer back in)."""
    meta = {e["entity_id"]: e for e in usable}
    valid = set(meta)
    buckets: dict[str, dict] = {}

    def _bucket(cat: str) -> dict:
        label, default_rel, icon = CATEGORIES[cat]
        return buckets.setdefault(cat, {
            "category": cat, "label": label, "icon": icon,
            "relevant": default_rel, "why": "", "entities": set()})

    for rc in raw_clusters:
        cluster_cat = normalize_category(rc.get("category") or rc.get("label"))
        rc_relevant = bool(rc.get("relevant"))
        why = str(rc.get("why") or "").strip()[:80]
        for eid in rc.get("entities", []):
            if eid not in valid:
                continue
            # trust the cluster's category, but if it fell to "other", try to
            # rescue a real category from the entity's own metadata/role.
            cat = cluster_cat
            if cat == "other":
                cat = _role_category(meta[eid])
            b = _bucket(cat)
            b["entities"].add(eid)
            if rc_relevant:
                b["relevant"] = True
            if not b["why"] and why:
                b["why"] = why

    out = []
    for b in buckets.values():
        ents = sorted(b["entities"])
        if not ents:
            continue
        out.append({**b, "entities": ents, "count": len(ents)})
    out.sort(key=lambda b: -b["count"])
    return out


def keepset_from(triage: dict, excluded_labels: set[str],
                 included_labels: set[str] | None = None) -> list[str]:
    """Recompute the kept entity set from a stored triage when the user toggles
    whole clusters in the review. A cluster is kept if it was relevant and not
    excluded, OR explicitly included (overriding an irrelevant verdict). Toggles
    are keyed by the cluster's category when present (stable across languages),
    else its label. The presence safety floor is always re-added."""
    included = included_labels or set()
    keep: set[str] = set()
    everything: set[str] = set()

    def _id(c: dict) -> str:
        return c.get("category") or c["label"]

    for c in triage.get("clusters", []):
        ents = c.get("entities", [])
        everything.update(ents)
        cid = _id(c)
        kept = (c.get("relevant") and cid not in excluded_labels) or cid in included
        if kept:
            keep.update(ents)
    keep |= {e for e in everything if is_person_tracker(e)}
    return sorted(keep)


async def triage_entities(repo, inventory: list[dict], advisor=None) -> dict:
    """Cluster the inventory, canonicalise into category buckets, derive the
    relevant shortlist. Stores the result in setting `entity_triage` and returns
    it. Pure-ish: one LLM call (or none) + one setting write."""
    usable = [e for e in inventory if not e.get("disabled")]
    valid = {e["entity_id"] for e in usable}

    raw: list[dict] = []
    by = "heuristic"
    if advisor is not None:
        try:
            raw = await advisor.cluster_entities(usable)
            by = "llm" if raw else "heuristic"
        except Exception:
            log.exception("entity clustering failed — heuristic triage")
    if not raw:
        raw = _heuristic_clusters(usable)
        by = "heuristic"

    clusters = _canonicalize(raw, usable)

    keep: set[str] = set()
    for c in clusters:
        if c.get("relevant"):
            keep.update(e for e in c.get("entities", []) if e in valid)
    # safety floor: never drop a real home/away tracker — losing presence is
    # catastrophic and these are few. Everything else is the model's call.
    floor = {e["entity_id"] for e in usable
             if is_person_tracker(e["entity_id"], e.get("friendly_name") or "")}
    keep |= floor

    result = {
        "by": by,
        "total": len(usable),
        "kept_count": len(keep),
        "kept": sorted(keep),
        "clusters": [
            {"label": c["label"], "category": c["category"], "icon": c["icon"],
             "relevant": bool(c.get("relevant")),
             "why": c.get("why", ""), "count": c["count"],
             "kept": sum(1 for e in c["entities"] if e in keep),
             # membership kept so the Sensors page can toggle a whole cluster
             # on/off and recompute the keep-set (not sent to the bubble cloud).
             "entities": c["entities"]}
            for c in clusters
        ],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    repo.set_setting("entity_triage", result)
    log.info("entity triage (%s): kept %d of %d entities across %d categories",
             by, len(keep), len(usable), len(clusters))
    return result

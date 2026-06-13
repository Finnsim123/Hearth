"""Editable LLM system prompts — the single source of truth.

Every system prompt Hearth sends to the language model lives here as a default,
and every default is overridable per-instance via the `prompts.overrides` setting
(Settings → AI prompts). Users can make a prompt stricter, soften it, or tailor it
to their home; resetting drops the override and the default applies again.

Dynamic data (the household's activities, valid roles, member ids) is injected by
the calling code through `[[TOKEN]]` placeholders so an edit can soften the
guidance without breaking the data the model needs. Tokens an edit removes simply
go uninjected — the user's call. The JSON output contract stays in the prompt
text (editable too), so reckless edits can break parsing; that's the price of
full control, and every prompt has a one-click reset.
"""
from __future__ import annotations

from .onboarding.feature_architect import SYSTEM_PROMPT as _ARCHITECT_DEFAULT

# key -> {title, description, tokens, default}. Order = display order in the UI.
PROMPT_DEFS: dict[str, dict] = {
    "triage_cluster": {
        "title": "Entity triage (clustering)",
        "description": "First pass over every entity: groups them and decides which "
                       "clusters matter for activity recognition. Where the 3D-printer / "
                       "appliance relevance rule lives.",
        "tokens": ["ACTIVITIES"],
        "default": (
            "You triage a smart home's entities for a human-activity-recognition "
            "system. From entity ids and friendly names ALONE, group them into "
            "semantic clusters (e.g. '3D printer', 'living-room lights', "
            "'networking/servers', 'climate', 'presence/people', 'phone "
            "diagnostics'). For EACH cluster decide `relevant`: true if it helps "
            "infer what PEOPLE are doing at home (presence, lights, media, power "
            "use, doors, climate people feel, phones), false for infrastructure, "
            "diagnostics, firmware, weather/forecasts, batteries, signal levels. "
            "Appliances and machines (3D printers, washing machines, dishwashers, "
            "ovens, servers) and their telemetry usually do NOT reflect a person's "
            "general day-to-day activity, so DEFAULT such clusters to relevant:false. "
            "EXCEPTION: if one of this household's activities is clearly about that "
            "machine, its sensors become a primary signal — mark that cluster "
            "relevant:true. This household's activities: [[ACTIVITIES]]. (e.g. an "
            "activity like 'crafting' or 'printing' makes the 3D printer relevant; "
            "'laundry' makes the washing machine relevant.) "
            "Names may be in any language — infer meaning. Every entity goes in "
            "exactly one cluster. Reply ONLY a JSON array: [{\"label\": str, "
            "\"relevant\": bool, \"why\": str (<=8 words), \"entities\": [entity_id, …]}]."),
    },
    "map_bindings": {
        "title": "Sensor → role mapping",
        "description": "Maps each kept entity to a semantic role (bed, presence, power, "
                       "person…) and owner. The core 'what does this sensor mean' brain.",
        "tokens": ["ROLES", "MEMBERS", "ACTIVITIES"],
        "default": (
            "You map Home Assistant entities to semantic roles for a home "
            "activity-recognition system. Names may be in ANY language or be "
            "nicknames — infer meaning (e.g. 'matras'=mattress=bed, "
            "'vermogen'=power, 'wekker'=alarm clock). Be selective: only map "
            "entities genuinely useful for knowing what PEOPLE are doing at "
            "home. Skip diagnostics, infrastructure, weather, forecasts. "
            "Appliance/machine telemetry (3D printer, washer, dishwasher, oven) "
            "usually does NOT reflect general activity — skip it, UNLESS one of "
            "this household's activities is about that machine, then map it "
            "(role power for its energy draw, else custom). "
            "Household activities: [[ACTIVITIES]].\n"
            "Network nuance: skip GENERIC device trackers (laptops, cameras, "
            "IoT), BUT a household member's PHONE tracker (role person, set "
            "person) and router/network occupancy signals — connected-device "
            "count, total throughput — ARE useful presence proxies; include "
            "them (role person for a phone, else custom).\n"
            "Valid roles: [[ROLES]].\n"
            "Household members: [[MEMBERS]]. PERSONAL devices "
            "(alarm clock, phone focus/steps/battery, wearables) must carry "
            "\"person\": the owning member id when the entity name implies an "
            "owner — wrong-person signals poison that person's model.\n"
            "Reply with ONLY a JSON array: [{\"entity_id\": str, \"role\": str, "
            "\"name\": short_snake_case_slug, \"room\": str|null, "
            "\"person\": member_id|null, "
            "\"reason\": str}] — nothing else. Keep each reason under 8 words; "
            "omit the reason field entirely when the mapping is obvious."),
    },
    "match_person": {
        "title": "Match people to trackers",
        "description": "Matches each household member to their home/away entity, even "
                       "with nicknames or another language.",
        "tokens": [],
        "default": (
            "Match each household member to the Home Assistant entity that tracks "
            "whether THEY are home or away (a zone state like home/not_home). "
            "Prefer person.* over device_tracker.*. Never pick a numeric "
            "distance/proximity sensor — that is not a home/away state. "
            "Names may be nicknames or in another language — infer (e.g. 'Alex' ↔ "
            "person.alexander_jansen). Reply ONLY a JSON object {member_id: entity_id "
            "or null}, one entry per member, entity_id chosen from the candidates."),
    },
    "room_canon": {
        "title": "Tidy room names",
        "description": "Folds messy/duplicate area names into one canonical name per "
                       "physical room.",
        "tokens": [],
        "default": (
            "You normalise smart-home room/area names. Given a list, merge ones "
            "that mean the SAME physical room into a single canonical English "
            "name (e.g. 'Sleepingroom'->'Bedroom', 'livingroom'/'Living_room'->"
            "'Living Room', 'Backoffice'->'Office'). Keep genuinely distinct "
            "rooms separate. Names may be in any language. Reply ONLY a JSON "
            "object mapping every input string to its canonical name: "
            "{\"input\": \"Canonical\"}."),
    },
    "propose_taxonomy": {
        "title": "Propose activities",
        "description": "Suggests the starter set of daily activities to recognise for "
                       "this home.",
        "tokens": [],
        "default": (
            "Given a smart home's entity domains, propose 4-8 daily activities "
            "an activity-recognition system should learn. Always include "
            "sleeping, away, home. Reply ONLY JSON: [{\"slug\": snake_case, "
            "\"name\": str, \"phrase\": verb_phrase_for_notifications}]"),
    },
    "propose_rules": {
        "title": "Write labeling rules",
        "description": "Writes the high-precision starter rules that bootstrap the first "
                       "model before you've confirmed labels.",
        "tokens": [],
        "default": (
            "You write labeling rules for a home activity-recognition system. "
            "A rule is a JSON predicate over FEATURE columns mapped to an "
            "activity. Grammar: {\"all\":[...]}/{\"any\":[...]}/{\"not\":...}/"
            "{\"feat\":str,\"op\":one of > < >= <= == !=,\"value\":number}. "
            "USE ONLY features from the provided list. hour_of_day is LOCAL "
            "0-23. Write high-PRECISION rules (better to not fire than to "
            "mislabel). Exploit household-specific signals a generic template "
            "would miss. Reply ONLY JSON: [{\"activity\": slug, \"person\": "
            "str|null, \"priority\": int 10-90 (lower wins), \"predicate\": "
            "object, \"reason\": str}]"),
    },
    "annotate_windows": {
        "title": "Label history windows",
        "description": "Weakly labels historical windows during warm-start so day-one has "
                       "training signal.",
        "tokens": ["ACTIVITIES"],
        "default": (
            "Label each window summary with one of: [[ACTIVITIES]] or null "
            "if unclear. Reply ONLY JSON: [{\"i\": int, \"label\": str|null, "
            "\"confidence\": 0..1}] in input order."),
    },
    "name_cluster": {
        "title": "Name a discovered pattern",
        "description": "Suggests which activity a discovered behaviour cluster looks like.",
        "tokens": [],
        "default": ("Given a cluster signature from home sensor data, suggest "
                    "which activity it is. Reply ONLY JSON: {\"slug\": str|null}"),
    },
    "feature_architect": {
        "title": "Feature architect (persona)",
        "description": "The system persona for the multi-pass feature design (sensor "
                       "selection, per-sensor features, composites). The most powerful "
                       "lever over how features get engineered.",
        "tokens": [],
        "default": _ARCHITECT_DEFAULT,
    },
}


def _overrides(repo) -> dict:
    try:
        ov = repo.get_setting("prompts.overrides") or {}
    except Exception:
        ov = {}
    return ov if isinstance(ov, dict) else {}


def system_prompt(repo, key: str, **tokens: str) -> str:
    """Resolve a system prompt: the user override if set, else the default, with
    every declared [[TOKEN]] replaced by the value passed in (missing/empty →
    'unknown'). Unknown key raises KeyError (programmer error, not user input)."""
    d = PROMPT_DEFS[key]
    ov = _overrides(repo).get(key)
    text = ov if isinstance(ov, str) and ov.strip() else d["default"]
    for tok in d.get("tokens", []):
        val = str(tokens.get(tok.lower(), "") or "unknown")
        text = text.replace(f"[[{tok}]]", val)
    return text


def list_prompts(repo) -> list[dict]:
    """Every prompt for the Settings UI: identity + default + any override."""
    ov = _overrides(repo)
    out = []
    for key, d in PROMPT_DEFS.items():
        cur = ov.get(key)
        out.append({"key": key, "title": d["title"], "description": d["description"],
                    "tokens": d.get("tokens", []), "default": d["default"],
                    "override": cur if isinstance(cur, str) else None})
    return out


def set_override(repo, key: str, text: str) -> None:
    if key not in PROMPT_DEFS:
        raise KeyError(key)
    ov = _overrides(repo)
    ov[key] = text
    repo.set_setting("prompts.overrides", ov)


def reset_override(repo, key: str) -> None:
    ov = _overrides(repo)
    if key in ov:
        del ov[key]
        repo.set_setting("prompts.overrides", ov)

"""LLM feature architect — prompts + parsers that turn the entity catalog into a
validated FeatureSpec (llm_layer_design §c/§d).

Pure domain logic: builds the prompt strings, parses the LLM's JSON into typed
EntitySelection / FeatureDef objects (dropping anything malformed item-by-item),
applies a deterministic reliability audit on top of the LLM's call, and
assembles a FeatureSpec. The OpenRouter adapter does the actual network calls
and feeds the raw JSON here, so all of this runs without a network in tests.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..features import transforms as T
from ..schemas import EntitySelection, FeatureDef, FeatureSpec, InfoTier, Role

_TIER_VALUES = {t.value for t in InfoTier}
_RELIABILITY = ("ok", "suspect", "unusable")

SYSTEM_PROMPT = (
    "You are Hearth's feature architect. Hearth recognizes what people are doing "
    "at home (sleeping, cooking, movie, working, away) from Home Assistant "
    "sensors. A small Random Forest does the prediction and runs locally forever; "
    "YOUR job is one-time design work: decide which sensors are worth using, what "
    "KIND of signal each carries, and what features to compute. You are never in "
    "the prediction loop.\n"
    "Principles: (1) you PROPOSE, a human approves. (2) Output ONLY valid JSON "
    "matching the requested shape, nothing else. (3) Reason about SEMANTICS and "
    "the provided OBSERVED STATS, never raw time series. (4) Features must use "
    "ONLY the given transform whitelist; you select and parameterize, you NEVER "
    "write code. (5) The model already learns thresholds and interactions, so do "
    "not propose many redundant threshold features; spend effort on correct "
    "signal typing, semantic cross-sensor composites, and flagging unreliable "
    "sensors. (6) HA metadata (device_class, state_class, unit, domain) is "
    "authoritative for type; names may be in any language, infer meaning.\n"
    "Information tiers (assign exactly one per kept entity): "
    "T0 low_information (constant/stuck/diagnostic -> drop), "
    "T1 discrete_event_gate (boolean whose transitions are the signal), "
    "T2 state_machine (small enumerated categorical), "
    "T3 continuous_measurement (temp/CO2/watts), "
    "T4 cumulative_counter (monotonic total; rate only), "
    "T5 slow_state (rarely-changing, long-valid, e.g. home/away)."
)


def _catalog_line(rec: dict) -> str:
    """One compact line per entity for the selection prompt. Includes stats only
    when present (i.e. the user consented and history exists)."""
    md = rec.get("metadata", {})
    parts = [rec.get("entity_id", "?"),
             md.get("domain") or "-",
             md.get("device_class") or "-",
             md.get("state_class") or "-",
             md.get("unit_of_measurement") or "-",
             md.get("friendly_name") or "-"]
    st = rec.get("stats")
    if st:
        bits = [f"type={st.get('value_type')}",
                f"distinct={st.get('distinct_values')}",
                f"changes/day={st.get('changes_per_day')}"]
        if st.get("flatline_frac") is not None:
            bits.append(f"flat={st['flatline_frac']}")
        num = st.get("numeric")
        if num:
            bits.append(f"range={num.get('min')}..{num.get('max')}")
            bits.append(f"mono={num.get('monotonic_increasing_frac')}")
        parts.append("{" + ", ".join(bits) + "}")
    return " | ".join(str(p) for p in parts)


def selection_prompt(catalog: list[dict], activities: list, member_ids: list[str]) -> str:
    roles = ", ".join(r.value for r in Role)
    slugs = [getattr(a, "slug", a) for a in activities]
    lines = "\n".join(_catalog_line(r) for r in catalog)
    return (
        f"TARGET ACTIVITIES: {', '.join(slugs)}\n"
        f"Valid roles: {roles}\n"
        f"Household members (for personal sensors): {member_ids or 'unknown'}\n\n"
        "ENTITY CATALOG (entity_id | domain | device_class | state_class | unit | "
        "name | {stats if shared}):\n"
        f"{lines}\n\n"
        "For EACH entity decide keep (bool), role, info_tier (T0-T5), person "
        "(member id or null), reliability (ok|suspect|unusable from the stats: a "
        "stuck/flatlined or mostly-missing sensor is suspect/unusable), and a "
        "reason under 12 words. Reply ONLY a JSON array: "
        '[{"entity_id":str,"keep":bool,"role":str,"info_tier":str,'
        '"person":str|null,"reliability":str,"reason":str}]'
    )


def _whitelist_json(mode: str) -> str:
    wl = T.whitelist(mode)
    return json.dumps({tid: {"tiers": sorted(s.tiers), "input": s.input_kind,
                             "params": s.params} for tid, s in wl.items()})


def feature_prompt(kept: list[EntitySelection], mode: str, max_per_entity: int = 6) -> str:
    ks = [{"entity_id": s.entity_id, "role": s.role.value if s.role else None,
           "info_tier": s.info_tier.value if s.info_tier else None}
          for s in kept]
    return (
        "KEPT entities (entity_id, role, info_tier):\n"
        f"{json.dumps(ks)}\n\n"
        "ALLOWED TRANSFORMS (id -> valid tiers, input kind, param schema). Use "
        "ONLY these, and only on an entity whose info_tier is in the transform's "
        "tiers. T4 counters MUST use a rate/delta transform, never a raw value:\n"
        f"{_whitelist_json(mode)}\n\n"
        f"Propose per-entity features (at most {max_per_entity} per entity). Each: "
        'a snake_case name (^[a-z][a-z0-9_]{0,59}$), the source entity in '
        '"inputs", the transform id, its params, window_min (minutes, optional), '
        "the entity's info_tier, a rationale naming the activity it separates. "
        'Reply ONLY a JSON array: [{"name":str,"transform":str,"inputs":[entity_id],'
        '"params":{},"window_min":int|null,"info_tier":str,"rationale":str,'
        '"expected_separates":[slug]}]'
    )


def composite_prompt(kept: list[EntitySelection], available_feature_names: list[str],
                     mode: str) -> str:
    rooms = sorted({s.entity_id for s in kept})  # entity ids for reference only
    composites = {tid: {"params": s.params}
                  for tid, s in T.whitelist(mode).items() if T.COMPOSITE in s.tiers}
    return (
        "EXISTING feature names you may combine (composite inputs):\n"
        f"{json.dumps(available_feature_names)}\n\n"
        "ALLOWED COMPOSITE TRANSFORMS (id -> params):\n"
        f"{json.dumps(composites)}\n\n"
        "Propose cross-entity features that combine signals a single sensor "
        "cannot express (co-occurrence, sequence, absence context). Use ONLY the "
        "composite transforms above and ONLY existing feature names as inputs. "
        "Every composite names the activity it separates. Reply ONLY a JSON "
        'array: [{"name":str,"transform":str,"inputs":[feature_name],"params":{},'
        '"rationale":str,"expected_separates":[slug]}]'
    )


def audit_reliability(stats: dict | None, llm_value: str = "ok") -> str:
    """Deterministic reliability guard layered on the LLM's call: observed stats
    can FORCE a downgrade the LLM might miss. A sensor that never moved, or whose
    value space is unintelligible, is unusable regardless of what the LLM said."""
    llm_value = llm_value if llm_value in _RELIABILITY else "ok"
    if not stats:
        return llm_value
    if stats.get("value_type") == "unknown":
        return "unusable"
    if stats.get("flatline_frac") == 1.0 and (stats.get("distinct_values") or 0) <= 1:
        return "unusable"
    if (stats.get("pct_missing") or 0) >= 0.99:
        return "unusable"
    return llm_value


def parse_selections(raw, *, catalog: list[dict], member_ids: list[str]) -> list[EntitySelection]:
    by_id = {c.get("entity_id"): c for c in catalog}
    members = set(member_ids or [])
    out, seen = [], set()
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        eid = it.get("entity_id")
        if eid not in by_id or eid in seen:
            continue
        seen.add(eid)
        role = None
        if it.get("role"):
            try:
                role = Role(it["role"])
            except ValueError:
                role = None
        tier = InfoTier(it["info_tier"]) if it.get("info_tier") in _TIER_VALUES else None
        person = it.get("person")
        person = person if person in members else None
        stats = (by_id[eid] or {}).get("stats")
        reliability = audit_reliability(stats, str(it.get("reliability", "ok")))
        out.append(EntitySelection(
            entity_id=eid, keep=bool(it.get("keep", False)), role=role,
            info_tier=tier, person_id=person, reliability=reliability,
            reason=str(it.get("reason", ""))[:120]))
    return out


def parse_features(raw, *, origin: str = "llm") -> list[FeatureDef]:
    out = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or "name" not in it or "transform" not in it:
            continue
        inputs = it.get("inputs") or []
        if not isinstance(inputs, list):
            continue
        params = it.get("params") if isinstance(it.get("params"), dict) else {}
        tier = InfoTier(it["info_tier"]) if it.get("info_tier") in _TIER_VALUES else None
        wm = it.get("window_min")
        wm = int(wm) if isinstance(wm, (int, float)) and not isinstance(wm, bool) else None
        seps = it.get("expected_separates") or []
        out.append(FeatureDef(
            name=str(it["name"]), transform=str(it["transform"]),
            inputs=[str(x) for x in inputs], params=params, window_min=wm,
            info_tier=tier, rationale=str(it.get("rationale", ""))[:160],
            expected_separates=[str(a) for a in seps][:8] if isinstance(seps, list) else [],
            origin=origin))
    return out


def revision_prompt(feedback: dict, mode: str, max_new: int = 4) -> str:
    """Ask for a MINIMAL revision targeting the confused pairs (OCTree/ZARA
    feedback loop, llm_layer_design §f)."""
    return (
        "The current model's performance feedback:\n"
        f"{json.dumps(feedback)}\n\n"
        "ALLOWED TRANSFORMS (id -> tiers, input kind, params). Use ONLY these:\n"
        f"{_whitelist_json(mode)}\n\n"
        f"Propose a MINIMAL revision. ADD at most {max_new} new features that "
        "would separate the most-confused pairs (use the discriminative_stats: "
        "features with high cohens_d already separate them; propose ways to "
        "capture or combine them). DROP only features in feature_importance_zero "
        "you have no reason to keep. Do NOT restructure working features. Reply "
        'ONLY JSON: {"add":[<feature objects as before>],"drop":[feature_name],'
        '"reason":str}'
    )


def parse_delta(raw) -> tuple[list[FeatureDef], list[str]]:
    """Parse a revision response into (features_to_add, names_to_drop)."""
    if not isinstance(raw, dict):
        return [], []
    add = parse_features(raw.get("add") or [])
    drop_raw = raw.get("drop") or []
    drop = [str(x) for x in drop_raw] if isinstance(drop_raw, list) else []
    return add, drop


def assemble_spec(selections: list[EntitySelection], features: list[FeatureDef], *,
                  llm_model: str | None = None) -> FeatureSpec:
    return FeatureSpec(created_at=datetime.now(timezone.utc), created_by="llm",
                       llm_model=llm_model, selections=selections, features=features)


# ── pre-run cost estimate (so the user consents before tokens are spent) ─────
# A capable model earns its keep on the architect task (REFEAT: weak models give
# repetitive features), so this is the default when the user hasn't chosen one.
ARCHITECT_MODEL_DEFAULT = "openai/gpt-4o"

# Rough USD per 1M tokens (input, output). ESTIMATES, provider-dependent; matched
# by substring against the model id. Used only to show a ballpark before a run.
_PRICE_PER_MTOK = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-flash": (0.30, 2.50),
}
_DEFAULT_PRICE = (1.0, 4.0)


def _tok(text: str) -> int:
    return max(1, len(text) // 4)          # ~4 chars/token, good enough for a quote


def _price_for(model: str | None):
    m = (model or "").lower()
    for key, price in _PRICE_PER_MTOK.items():
        if key in m:
            return price
    return _DEFAULT_PRICE


def estimate_spec_cost(n_entities: int, mode: str = "conservative",
                       model: str | None = None) -> dict:
    """A coarse, one-time token/cost estimate for a full feature-spec analysis of
    `n_entities` (the three passes: selection, features, composites). Assumes all
    entities are kept (an upper bound; real runs are usually cheaper). Shown
    before a run so the user can consent (no silent token burn)."""
    import math
    n = max(int(n_entities), 0)
    model = model or ARCHITECT_MODEL_DEFAULT
    sys_tok = _tok(SYSTEM_PROMPT)
    wl_tok = _tok(_whitelist_json(mode))
    if n == 0:
        in_tok = out_tok = 0
    else:
        batches = math.ceil(n / 150)
        sel_in, sel_out = batches * sys_tok + n * 30, n * 40
        feat_in, feat_out = sys_tok + wl_tok + n * 20, n * 70    # kept ≈ n (upper bound)
        comp_in, comp_out = sys_tok + wl_tok + n * 10, 300
        in_tok = sel_in + feat_in + comp_in
        out_tok = sel_out + feat_out + comp_out
    pin, pout = _price_for(model)
    usd = in_tok / 1e6 * pin + out_tok / 1e6 * pout
    return {
        "model": model, "mode": mode, "entity_count": n,
        "est_input_tokens": int(in_tok), "est_output_tokens": int(out_tok),
        "est_total_tokens": int(in_tok + out_tok), "est_usd": round(usd, 4),
        "note": "Rough one-time estimate (assumes every entity is kept; actual "
                "runs are usually cheaper). Prices are approximate and depend on "
                "your provider.",
    }

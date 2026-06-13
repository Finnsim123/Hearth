# LLM Data Analytics Layer: Full Design (Step 3)

Status: design proposal, June 2026. Read only on the repo. Propose, do not implement.
This is the core deliverable. It specifies the complete framework by which the LLM selects entities and defines features, as a design time and maintenance time component that never touches the inference path (per the Step 2 recommendation and your cost model).

Design decisions carried in from your answers:
- Feature power: CAAFE style executable feature specification, with role plus composite selection as the conservative fallback (designed as two tiers of the same grammar so the safe subset is always available).
- LLM timing: onboarding (one shot) plus on demand plus a scheduled entity discovery and maintenance pass, gated by explicit user approval so adding a test sensor never silently burns tokens or retrains.
- Aggregate stats to the LLM: a user yes/no decision with implications spelled out, not a hardcoded default. The whole layer must function (degraded) when the user says no.

The layer has six specified parts: (a) entity catalog schema, (b) information tier taxonomy, (c) prompt templates, (d) output contract and safe execution, (e) data budgeting, (f) feedback loop and stopping criterion.

A naming note to avoid confusion with the existing code: Hearth's current LLM advisor maps entities to a fixed `Role` enum and proposes rules. This design keeps the `Role` typing (it drives forward fill, evidence tier, and the existing recipe defaults) and adds a feature specification layer on top. Roles remain the coarse semantic type; the information tier and the feature spec are the new finer grained machinery.

---

## (a) Entity catalog schema

The entity catalog is the single structured artifact the LLM reads. It extends Hearth's existing entity inventory (`DATA_MODEL.md` section 4) with the fields the feature architect and the reliability auditor need. One record per HA entity. Built deterministically from HA (`/api/states`, entity registry, area registry) plus, when history exists, aggregate statistics from InfluxDB or the HA recorder.

Privacy gating: the `stats` and `samples` blocks are populated only if the user has consented to sharing aggregate statistics (the yes/no toggle). When consent is no, those blocks are null and the LLM works from `metadata` alone (degraded mode, equivalent to a fresh install with no history).

JSON Schema (draft 2020-12) for one catalog record:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "hearth.entity_catalog_record.v1",
  "type": "object",
  "required": ["entity_id", "metadata"],
  "additionalProperties": false,
  "properties": {
    "entity_id": {"type": "string", "description": "HA entity id, e.g. binary_sensor.presence_sensor_sofa"},
    "metadata": {
      "type": "object",
      "required": ["domain"],
      "additionalProperties": false,
      "properties": {
        "domain": {"type": "string", "description": "HA domain: sensor, binary_sensor, media_player, person, ..."},
        "friendly_name": {"type": ["string", "null"]},
        "device_class": {"type": ["string", "null"], "description": "HA device_class: occupancy, power, temperature, ..."},
        "state_class": {"type": ["string", "null"], "enum": ["measurement", "total", "total_increasing", null], "description": "HA state_class: drives counter vs measurement reasoning"},
        "unit_of_measurement": {"type": ["string", "null"]},
        "area": {"type": ["string", "null"], "description": "HA area, canonicalized"},
        "device": {
          "type": ["object", "null"],
          "properties": {
            "manufacturer": {"type": ["string", "null"]},
            "model": {"type": ["string", "null"]}
          }
        },
        "entity_category": {"type": ["string", "null"], "enum": ["config", "diagnostic", null], "description": "HA entity_category: diagnostic strongly implies skip"},
        "disabled": {"type": "boolean", "default": false},
        "hidden": {"type": "boolean", "default": false}
      }
    },
    "stats": {
      "type": ["object", "null"],
      "description": "Null when no history OR user declined stat sharing.",
      "additionalProperties": false,
      "properties": {
        "window_days": {"type": "number", "description": "Observation span these stats cover"},
        "value_type": {"type": "string", "enum": ["boolean", "enum", "numeric_continuous", "numeric_discrete", "string", "unknown"]},
        "distinct_values": {"type": "integer", "description": "Observed cardinality"},
        "top_states": {
          "type": ["array", "null"],
          "description": "For boolean/enum: most frequent states with frequency. Capped at 8.",
          "items": {
            "type": "object",
            "properties": {
              "value": {"type": "string"},
              "frac": {"type": "number"}
            }
          }
        },
        "numeric": {
          "type": ["object", "null"],
          "description": "For numeric types only.",
          "properties": {
            "min": {"type": "number"},
            "p05": {"type": "number"},
            "median": {"type": "number"},
            "p95": {"type": "number"},
            "max": {"type": "number"},
            "monotonic_increasing_frac": {"type": "number", "description": "Fraction of consecutive deltas >= 0; near 1.0 implies a cumulative counter"}
          }
        },
        "changes_per_day": {"type": "number", "description": "Update/state-change frequency"},
        "median_seconds_between_changes": {"type": ["number", "null"]},
        "active_hours_hist": {"type": "array", "items": {"type": "number"}, "minItems": 24, "maxItems": 24, "description": "Normalized 24-bin histogram of when this entity changes"},
        "pct_missing": {"type": "number", "description": "Fraction of the observation grid with no value (after expected cadence)"},
        "longest_gap_hours": {"type": ["number", "null"], "description": "Longest stretch with no update; drives staleness/reliability"},
        "flatline_frac": {"type": ["number", "null"], "description": "Fraction of time the value never changed within its expected cadence; high = stuck sensor"},
        "last_changed_age_hours": {"type": ["number", "null"], "description": "How long since the entity last changed at catalog build time"}
      }
    },
    "samples": {
      "type": ["array", "null"],
      "description": "A few recent (timestamp, state) pairs for the LLM to see real values. Null if stats sharing declined. Strings truncated to 32 chars. No more than 5.",
      "items": {
        "type": "object",
        "properties": {
          "ts_local": {"type": "string"},
          "state": {"type": ["string", "number", "null"]}
        }
      }
    },
    "current_binding": {
      "type": ["object", "null"],
      "description": "Present only on a re-analysis: what Hearth currently does with this entity, so the LLM revises rather than restarts.",
      "properties": {
        "role": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "enabled": {"type": "boolean"},
        "model_importance": {"type": ["number", "null"], "description": "Summed RF importance of this binding's features in the current model"}
      }
    }
  }
}
```

Notes on the new fields versus today's inventory, and why each earns its place:

- `state_class` and `entity_category` are pulled from HA but not currently used. `state_class: total_increasing` plus `monotonic_increasing_frac` near 1.0 is the deterministic signature of a cumulative counter (energy kWh, step count), which needs a delta transform, never a raw value. `entity_category: diagnostic` is HA's own statement that the entity is machinery, a strong prune signal that is more reliable than the regex blocklist.
- `numeric` percentiles (not just min and max) let the LLM reason about thresholds robustly (a power sensor with p95 of 40 W and max of 2000 W has a rare spike the LLM should know about).
- `pct_missing`, `longest_gap_hours`, `flatline_frac`, `last_changed_age_hours` are the reliability auditor's raw material (used in section f and the tier in section b). `flatline_frac` near 1.0 on a sensor that should vary is the classic stuck sensor signature.
- `current_binding.model_importance` closes the OCTree feedback loop: on re analysis the LLM sees how much the live model actually used each entity, so it can demote dead weight and defend high value sensors.

This schema is the contract Stage 2 emits and Stage 3 (feature builder) and the LLM both consume. It is versioned (`v1` in `$id`) so changes are explicit.

---

## (b) Information tier taxonomy

This formalizes your gate versus environment intuition into a typed classification the LLM assigns to every selected entity. The tier is orthogonal to the existing `Role` (role says "this is a presence sensor"; tier says "this is a discrete event gate"). The tier determines which feature families are appropriate, which is what the feature spec (section d) draws from.

Six information tiers:

| Tier | Name | Definition | Catalog signature | Appropriate feature families |
|---|---|---|---|---|
| T1 | discrete_event_gate | Boolean or low cardinality state whose transitions are themselves the signal; a first tier state change gate. | value_type boolean; distinct_values 2; device_class occupancy/motion/door/presence; moderate changes_per_day. | occupancy fraction, any flag, transition count, time since last change (idleness), debounced on/off, on/off run length. |
| T2 | state_machine | Enumerated categorical with a small set of meaningful named states. | value_type enum; distinct_values 3 to ~12; e.g. media_player (playing/paused/idle), alarm panel, thermostat mode. | one hot per state, dwell fraction per state, current state at window end, transition count, last state. |
| T3 | continuous_measurement | A continuously varying physical quantity; environmental context. | value_type numeric_continuous; state_class measurement; unit like degC, ppm, lx, percent, W. | window mean, max, min, delta (end minus start), slope, last value, deviation from daily baseline. |
| T4 | cumulative_counter | A monotonically increasing total; only its rate of change carries activity signal. | state_class total_increasing; monotonic_increasing_frac near 1.0; unit kWh, steps. | delta over window, rate per minute. Never raw value (it leaks time and drifts). |
| T5 | slow_state | A state that changes rarely and stays valid for a long time; needs long lookback. | very low changes_per_day; long median_seconds_between_changes; e.g. person home/away, presence at the day scale. | last value with long forward fill, home fraction over long lookback, time since last transition. (Maps to the existing slow_sensor flag.) |
| T0 | low_information | Constant, near constant, stuck, diagnostic, or otherwise carrying no activity signal. Selected out. | flatline_frac near 1.0; distinct_values 1; entity_category diagnostic; pct_missing near 1.0; on the blocklist. | none. Excluded from the feature set, recorded with a reason. |

How the LLM assigns a tier: it is told the six tiers with their signatures, given the catalog record, and must return exactly one tier per selected entity plus a one line reason. The tier assignment is deterministically checkable (for example T4 requires `state_class total_increasing` or `monotonic_increasing_frac > 0.95`; a T4 claim failing that check is rejected and the entity is re queued for human review), which makes the tier both an LLM judgment and a validated field, the same propose plus validate discipline Hearth already uses for rules.

Relationship to the existing evidence tiers (`features/evidence.py`: 1 direct, 2 behavioral, 3 ambient, 0 prior): these are not the same axis and both should exist. The evidence tier answers "how much should I trust a prediction that rests on this?" (a runtime trust question, already implemented). The information tier answers "what kind of feature should I compute from this?" (a design time construction question, new here). A bed sensor is evidence tier 1 (direct) and information tier T1 (discrete event gate); a CO2 sensor is evidence tier 3 (ambient) and information tier T3 (continuous measurement). They correlate but are distinct, and conflating them would lose information. Judgment: keep both; the feature spec carries the information tier, the prediction carries the evidence tier.

---

## (c) Prompt templates

Model and temperature: the existing adapter defaults to `openai/gpt-4o-mini` at temperature 0, chunked, with strict JSON validation. For the feature architect work I recommend a stronger reasoning model as the default for the three design time tasks below (the user already selects the model in the wizard and Settings, so this is a default, not a lock), and temperature 0 throughout (feature engineering wants determinism and reproducibility, not creativity). Keep `gpt-4o-mini` as the floor for the cheap tasks (cluster naming, room canonicalization). Rationale: REFEAT's finding that weaker models produce repetitive, low diversity features (Step 2 Part A) argues for a capable model on the architect task specifically; the cost is bounded because these calls are design time and rare.

All three task prompts share one system prompt. Injection points are written as `{{ double_brace }}`.

### System prompt (the feature architect persona)

```
You are Hearth's feature architect. Hearth is a local, privacy-first system that
recognizes what people are doing at home (sleeping, cooking, watching a movie,
working, away) from Home Assistant sensors. A small Random Forest does the actual
prediction and runs locally forever; YOUR job is one-time design work: decide which
sensors are worth using, what KIND of signal each carries, and what features to
compute from each. You are never in the prediction loop.

Core principles you must follow:
1. You PROPOSE; a human approves. Never assume your output is applied directly.
2. Output ONLY valid JSON matching the requested schema. No prose outside the JSON.
3. You reason about SEMANTICS (what a sensor means in a home) and about OBSERVED
   BEHAVIOR (the statistics provided), never about raw time series.
4. Features must be computable by a deterministic builder from a fixed transform
   whitelist. You select and parameterize transforms; you NEVER write code.
5. The downstream model is a Random Forest. It already learns thresholds and
   interactions on its own, so do NOT propose many redundant threshold features.
   Spend your budget on (a) correct signal typing, (b) semantic cross-sensor
   composites a model cannot invent without world knowledge, and (c) flagging
   sensors that are unreliable or carry no information.
6. Prefer fewer, well-justified features over many. Every feature carries a
   one-line rationale tied to an activity it helps separate.
7. Home Assistant metadata is authoritative for type: device_class, state_class,
   unit_of_measurement, domain. Use them first; use names only to disambiguate.
   Names may be in any language or be nicknames; infer meaning.

Information tiers you assign to each selected entity (exactly one):
- T1 discrete_event_gate: boolean/low-card state whose transitions are the signal
  (occupancy, door, motion, bed-occupied).
- T2 state_machine: small enumerated categorical (media playing/paused/idle).
- T3 continuous_measurement: continuously varying physical quantity (temp, CO2, lux, watts).
- T4 cumulative_counter: monotonically increasing total; only its rate matters (kWh, steps).
- T5 slow_state: rarely-changing, long-valid state needing long lookback (person home/away).
- T0 low_information: constant/stuck/diagnostic/no signal; select OUT with a reason.

Reliability: if observed statistics indicate a sensor is unreliable (stuck/flatlined,
mostly missing, long gaps, or behaving unlike its device_class implies), say so and
lower or withhold your reliance on it. A sensor predicted to vary that never varies
is suspect.
```

### Task prompt 1: entity relevance and selection (given target activity classes)

Injects the target activities and the catalog (batched, see section e). Returns a selection decision per entity.

```
TARGET ACTIVITIES this home wants to recognize:
{{ activities_json }}   // e.g. [{"slug":"sleeping"},{"slug":"cooking"},{"slug":"movie"},{"slug":"working"},{"slug":"away"}]

ENTITY CATALOG (one record per entity; stats may be null if history/consent absent):
{{ catalog_batch_json }}

For EACH entity decide:
- keep: true if it can carry signal about ANY target activity for a PERSON at home,
  false for diagnostics, infrastructure, weather/forecast, machinery telemetry, or
  anything that says nothing about human activity.
- role: one of {{ roles_csv }}  // the existing Hearth role enum
- info_tier: one of T0,T1,T2,T3,T4,T5
- person: a household member id if this is a personal sensor (bed side, own phone),
  else null. Members: {{ member_ids }}.
- reliability: one of ok | suspect | unusable, based on the stats.
- reason: under 12 words.

Reply ONLY a JSON array:
[{"entity_id":str,"keep":bool,"role":str,"info_tier":str,"person":str|null,
  "reliability":"ok|suspect|unusable","reason":str}]
```

### Task prompt 2: per entity feature transform proposal (keyed to tier)

Runs over the kept entities. The whitelist of transforms is injected so the LLM can only choose from safe, executable operations (this is the CAAFE safe execution pattern adapted to a grammar instead of code).

```
You will propose features for these KEPT entities, grouped by info_tier:
{{ kept_entities_json }}   // each: {entity_id, role, info_tier, name, stats}

ALLOWED TRANSFORMS (you may ONLY use these; each lists its valid tiers and params):
{{ transform_whitelist_json }}   // see Output contract section (d)

Rules:
- Choose transforms VALID for the entity's info_tier (the whitelist states which).
- For T4 cumulative_counter you MUST use delta/rate, never raw value.
- Parameterize windows in minutes; respect per-role defaults unless the stats
  justify a change (state a reason if you deviate).
- Each feature: a stable snake_case name, the source entity, the transform id,
  its params, the info_tier, a rationale naming the activity it helps separate.
- Do NOT propose more than {{ max_features_per_entity }} features per entity.

Reply ONLY a JSON array of feature objects per the feature-spec schema.
```

### Task prompt 3: cross entity interaction features (co occurrence, sequence, room transitions)

Runs once over the full kept set (or per area batch). This is where the LLM earns its keep, because semantic composites are what an RF cannot invent.

```
KEPT entities with their roles, rooms, and info_tiers:
{{ kept_entities_compact_json }}

EXISTING base feature names available as composite inputs:
{{ available_feature_names_json }}

Propose CROSS-ENTITY features that combine signals a single sensor cannot express.
Focus on:
- co_occurrence: two or more signals true together (e.g. sofa occupied AND media
  playing AND lights low -> movie).
- sequence: one signal shortly after another (door opened THEN kitchen presence).
- room_transition: presence moving between rooms within the window.
- absence_context: a gate being OFF as context (no bed occupancy AND night).
Use ONLY the allowed composite transforms and ONLY existing feature names as inputs.
Propose generously but every composite must name the activity it separates.

Reply ONLY a JSON array of composite feature objects per the feature-spec schema.
```

How the catalog and targets are injected: the entity catalog is batched by area or domain (section e) so each call stays within context; the target activity list is small and injected whole into every call; the transform whitelist is injected verbatim so the model cannot invent transforms. All three tasks return arrays that are validated item by item and merged into one feature specification (section d). Failures degrade per item to the heuristic floor or are dropped, exactly as the current adapter does.

---

## (d) Output contract and safe execution

The LLM's entire output is one feature specification: a document a deterministic builder executes with zero further LLM calls. This is the artifact that makes "spend once" real.

### Feature specification schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "hearth.feature_spec.v1",
  "type": "object",
  "required": ["spec_version", "created_at", "created_by", "selections", "features"],
  "additionalProperties": false,
  "properties": {
    "spec_version": {"type": "string", "const": "v1"},
    "created_at": {"type": "string", "format": "date-time"},
    "created_by": {"type": "string", "enum": ["llm", "heuristic", "human", "llm+human"]},
    "llm_model": {"type": ["string", "null"]},
    "selections": {
      "type": "array",
      "description": "Per-entity keep/role/tier/reliability decisions (task 1).",
      "items": {
        "type": "object",
        "required": ["entity_id", "keep", "role", "info_tier", "reliability", "reason"],
        "properties": {
          "entity_id": {"type": "string"},
          "keep": {"type": "boolean"},
          "role": {"type": "string"},
          "info_tier": {"type": "string", "enum": ["T0", "T1", "T2", "T3", "T4", "T5"]},
          "person": {"type": ["string", "null"]},
          "reliability": {"type": "string", "enum": ["ok", "suspect", "unusable"]},
          "reason": {"type": "string", "maxLength": 120}
        }
      }
    },
    "features": {
      "type": "array",
      "description": "Executable feature definitions (tasks 2 and 3).",
      "items": {
        "type": "object",
        "required": ["name", "transform", "inputs", "info_tier", "rationale"],
        "additionalProperties": false,
        "properties": {
          "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,59}$"},
          "transform": {"type": "string", "description": "A transform id from the whitelist"},
          "inputs": {
            "type": "array",
            "description": "Source entity ids (for per-entity transforms) OR existing feature names (for composites).",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 6
          },
          "params": {
            "type": "object",
            "description": "Transform-specific params; validated against the transform's param schema.",
            "additionalProperties": true
          },
          "window_min": {"type": "integer", "minimum": 1, "maximum": 1440},
          "info_tier": {"type": "string", "enum": ["T1", "T2", "T3", "T4", "T5"]},
          "rationale": {"type": "string", "maxLength": 160},
          "expected_separates": {
            "type": "array",
            "description": "Activity slugs this feature is expected to help separate.",
            "items": {"type": "string"}
          },
          "origin": {"type": "string", "enum": ["llm", "heuristic", "human"], "default": "llm"}
        }
      }
    }
  }
}
```

### The transform whitelist (the safety boundary, CAAFE pattern)

This is the executable vocabulary. The LLM may only reference transform ids that exist here; the builder only knows how to execute these; anything else is rejected before execution. This is the direct analogue of CAAFE's controlled execution, except Hearth never runs LLM authored code, only parameterized calls to vetted functions. It is also the seam that gives you both tiers from your answer: the conservative fallback is "whitelist contains only role recipe selection plus existing composites"; the full power mode is "whitelist contains the parameterized transforms below." Same mechanism, different whitelist contents, switchable as a setting.

Each whitelist entry declares: id, valid info tiers, a param schema, and which input kind it takes (entity ids or feature names). Illustrative core set:

```json
[
  {"id": "occupancy_fraction", "tiers": ["T1"], "input": "entity", "params": {}},
  {"id": "any_active", "tiers": ["T1"], "input": "entity", "params": {}},
  {"id": "transition_count", "tiers": ["T1", "T2"], "input": "entity", "params": {}},
  {"id": "time_since_last_change", "tiers": ["T1", "T2", "T5"], "input": "entity", "params": {"cap_min": "int"}},
  {"id": "run_length_on", "tiers": ["T1"], "input": "entity", "params": {}},
  {"id": "state_onehot", "tiers": ["T2"], "input": "entity", "params": {"states": "list[str]"}},
  {"id": "state_dwell_fraction", "tiers": ["T2"], "input": "entity", "params": {"state": "str"}},
  {"id": "last_state", "tiers": ["T2", "T5"], "input": "entity", "params": {}},
  {"id": "window_mean", "tiers": ["T3"], "input": "entity", "params": {}},
  {"id": "window_max", "tiers": ["T3"], "input": "entity", "params": {}},
  {"id": "window_min", "tiers": ["T3"], "input": "entity", "params": {}},
  {"id": "window_delta", "tiers": ["T3", "T4"], "input": "entity", "params": {}},
  {"id": "window_slope", "tiers": ["T3"], "input": "entity", "params": {}},
  {"id": "deviation_from_daily_baseline", "tiers": ["T3"], "input": "entity", "params": {"baseline_days": "int"}},
  {"id": "counter_rate", "tiers": ["T4"], "input": "entity", "params": {}},
  {"id": "home_fraction", "tiers": ["T5"], "input": "entity", "params": {}},
  {"id": "co_occurrence_and", "tiers": ["composite"], "input": "feature", "params": {"threshold": "float"}},
  {"id": "co_occurrence_count", "tiers": ["composite"], "input": "feature", "params": {}},
  {"id": "sequence_within", "tiers": ["composite"], "input": "feature", "params": {"max_gap_min": "int"}},
  {"id": "room_transition_count", "tiers": ["composite"], "input": "feature", "params": {"rooms": "list[str]"}},
  {"id": "absence_and", "tiers": ["composite"], "input": "feature", "params": {}}
]
```

### Validation and whitelist scheme

Validation runs in this order, per feature, before anything is stored, mirroring and extending the existing `validate_predicate` walker:

1. Schema validation: the feature object matches `hearth.feature_spec.v1`.
2. Transform whitelist check: `transform` exists in the active whitelist.
3. Tier compatibility: the entity's assigned `info_tier` is in the transform's `tiers` (T4 must use delta or rate; a `window_mean` on a T4 counter is rejected).
4. Input existence and kind: for entity inputs, every id is a real catalog entity and is kept; for composite inputs, every name is a feature defined earlier in the spec or a known base feature. No dangling references.
5. Param schema check: params match the transform's declared schema and bounds (window_min within 1 to 1440, etc.).
6. Name uniqueness and pattern: `^[a-z][a-z0-9_]{0,59}$`, unique within the spec (collisions disambiguated, never dropped, as the current adapter does for binding names).
7. Reliability gate: features whose only input is a `reliability: unusable` entity are dropped; `suspect` entities are allowed but flagged in the UI.
8. Budget cap: total feature count capped (section e); lowest rationale quality or lowest expected separation features trimmed first if over budget.

Anything failing is dropped (logged with reason) and the layer degrades to the heuristic floor for that slot, never crashing. The builder then executes the validated spec deterministically; it is pure function evaluation over the resampled window, exactly the model `pipeline.py` already uses, just driven by a spec instead of a fixed registry. No `eval`, no LLM at build or inference time.

Relationship to the existing `feature_set_version()`: the feature spec is hashed into the version exactly as composites are today, so any spec change bumps the version and forces a clean retrain (no mixed version training, ADR-7 preserved).

---

## (e) How much data to feed, and how

Thousands of entities cannot fit in one context window, and dumping raw values both blows the budget and degrades quality (SensorLLM: LLMs handle raw numbers poorly). The strategy has four parts.

1. Pre filter deterministically before the LLM sees anything. The existing funnel already removes disabled, hidden, diagnostic, and blocklisted entities, and the physics gate removes stateless domains. This typically cuts the ~1700 entity example down to a few hundred (the repo cites ~248). The LLM never sees the rejected majority. This is free, fast, and shrinks the spurious correlation search space (RESEARCH.md, Grinsztajn 2022).

2. Send a compact catalog summary, never raw history. Per entity the LLM sees metadata plus the `stats` block plus at most 5 sample states. That is roughly 150 to 300 tokens per entity. Whether the `stats` and `samples` blocks are populated is the user's yes/no consent decision (see below). With consent off, each record is metadata only (~40 tokens), which is cheaper but blinds the reliability auditor.

3. Batch by area, then domain. Task 1 (selection) is batched at ~150 entities per call grouped by HA area, so co located sensors are reasoned about together (a presence sensor and a light in the same room inform each other). Task 2 (per entity features) batches the kept set similarly. Task 3 (composites) is the only one that benefits from breadth; run it per area for room level composites plus one whole home pass restricted to the top N kept entities by expected importance (the ZARA style retrieval: rank entities by discriminative potential for the target activities, send only the top slice). This keeps every call bounded while still allowing cross room composites for the entities that matter.

4. Decide raw values versus summary statistics explicitly. The rule: the LLM sees summary statistics and a handful of sample states, never the time series. Justification is threefold: privacy (raw history never leaves the box, the existing contract), cost (statistics are orders of magnitude smaller than series), and quality (SensorLLM and the tokenizer issue). The samples exist only to ground the LLM in what a real state looks like (so it knows `media_player` reads "playing" not "1"), not for it to compute anything from.

The aggregate stats consent decision (your requirement): present as an explicit yes/no in the wizard and Settings, with the implications spelled out per outcome. Proposed copy:

```
Share aggregate sensor statistics with the AI assistant?

[ Yes, share aggregate stats ]
  The assistant sees per-sensor summaries: how often each sensor changes,
  what range of values it takes, how often it is missing, and a few recent
  example states. It NEVER sees your raw history or a timeline of your
  activity. This lets it flag broken or unreliable sensors and choose better
  features, so your model is more accurate from day one.

[ No, metadata only ]
  The assistant sees only sensor names, types, and units (the same labels
  shown in Home Assistant). It cannot detect unreliable sensors and must
  guess feature choices from names alone. Most private option; slightly
  less accurate setup. You can change this later in Settings.
```

This is a per instance setting (a `connections.llm.options` field or a top level setting), defaulting to unset so the user is forced to choose during onboarding rather than silently opted in.

Budget caps (defaults, all settings): max 150 entities per selection call, max 6 features per entity, max ~250 total features in the spec (above which the RF gains little and overfit risk rises at Hearth's label counts), max 3 LLM rounds per feedback cycle (section f). These bound worst case token spend to a predictable, displayable number (the existing cost log in `_set_status` already tracks token usage; surface the estimate before the user confirms a run, per your "do not silently burn tokens" requirement).

---

## (f) Feedback loop and stopping criterion

This is the OCTree plus CAAFE iteration pattern, grounded in Hearth's existing model artifacts, and it is the heart of the maintenance pass you described. It runs at design time and on the scheduled maintenance trigger, never at inference.

### What gets summarized back to the LLM

After a model trains, Hearth already computes everything the loop needs (in `trainer.py` metrics and `evaluate.py`). The feedback summary sent to the LLM is a compact, structured digest:

```json
{
  "model_version": "alice-v7",
  "validation": {
    "accuracy_confirmed": 0.81,
    "accuracy_confirmed_ci": [0.72, 0.88],
    "n_confirmed": 64,
    "auc_macro": 0.86
  },
  "per_class": {
    "cooking": {"precision": 0.55, "recall": 0.40, "f1": 0.46, "support": 18},
    "movie":   {"precision": 0.88, "recall": 0.91, "f1": 0.89, "support": 73}
  },
  "confusion_top_pairs": [
    {"true": "cooking", "pred": "eating", "count": 11},
    {"true": "working", "pred": "movie", "count": 6}
  ],
  "feature_importance_top": [
    {"feature": "sofa_occupancy_fraction", "importance": 0.14},
    {"feature": "kitchen_presence_fraction", "importance": 0.09}
  ],
  "feature_importance_zero": ["bedroom_co2_window_slope", "hallway_lux_window_max"],
  "evidence_profile": {"T1_direct": 0.62, "behavioral": 0.25, "ambient": 0.13},
  "discriminative_stats": {
    "cooking_vs_eating": [
      {"feature": "stove_power_on", "cohens_d": 1.8},
      {"feature": "kitchen_presence_fraction", "cohens_d": 0.3}
    ]
  }
}
```

The `confusion_top_pairs` plus `discriminative_stats` block is the ZARA contribution: for each pair the model confuses, Hearth computes (deterministically, no LLM) which existing features are statistically discriminative (effect size such as Cohen's d, or the PSI machinery already in `evaluate.py`) and which are not. The LLM is then asked a focused question, not an open one.

### The revision prompt

```
The current model confuses these activity pairs (true -> predicted, count):
{{ confusion_top_pairs }}
For each confused pair, here are the features that already separate them well
and poorly (effect size):
{{ discriminative_stats }}
These features carry NO importance in the current model (candidates to drop):
{{ feature_importance_zero }}
The model's evidence rests {{ evidence_profile }} across signal tiers.

Propose a MINIMAL revision to the feature spec:
- ADD at most {{ max_new_features }} new features that would separate the most
  confused pairs, using ONLY whitelisted transforms and KEPT entities.
- DROP features in the zero-importance list ONLY if you have no reason to keep them.
- Do NOT restructure features that are working.
Reply ONLY a JSON object {"add":[...feature objects...],"drop":[...names...],
"reason": str}.
```

Each revision is applied (after validation), the model retrains, and the new model's confirmed accuracy CI is compared to the incumbent's via the existing `promotion_gate` (Wilson interval overlap). This reuses Hearth's machinery exactly: the automatic accuracy comparison you described IS the promotion gate.

### Stopping criterion

The loop stops on the first of these (all are settings):

1. No improvement: the new model's confirmed accuracy lower CI does not exceed the incumbent's lower CI by at least the gate margin (the existing 0.02). One non improving round ends the loop. This is the primary criterion and it directly reuses `promotion_gate`.
2. Round cap: maximum 3 revision rounds per cycle (bounds token spend; OCTree style loops can run forever, Hearth must not).
3. Diminishing confusion: the largest off diagonal confusion count falls below a floor (for example fewer than 5 windows), meaning there is no longer a clear pair to target.
4. Insufficient labels: if `n_confirmed` is below a threshold, the loop does not run at all (you cannot measure improvement on too few confirmed labels, RESEARCH.md P6); it waits for more feedback. This also prevents the cold start circularity flagged in the Step 1 audit, because the maintenance loop refuses to optimize against bootstrap only signal.

The cold start interaction (important): at first training there are zero confirmed labels, so criterion 4 holds and the feedback loop does not run. The initial spec is the onboarding LLM output, trained once, and promoted under whatever cold start policy you choose (this is the Step 1 risk; the feedback loop deliberately does not paper over it by optimizing against rules). The loop activates only once real confirmations accumulate, at which point every revision is judged on non circular, human confirmed accuracy.

### The scheduled maintenance trigger (your new-sensor requirement, fully specified)

Two distinct triggers, both off the inference path:

1. New entity discovery (cheap, LLM free, daily or hourly): the existing `inventory_sync` job (currently daily, `use_llm=False`) already picks up new HA entities. Extend it to diff against the known catalog and, if genuinely new bindable entities appear, raise a single UI prompt ("3 new sensors found. Add them to your model?") and a notification. It does NOT call the LLM and does NOT retrain. This is what stops a test sensor from burning tokens.
2. User approved integration (LLM, gated): only when the user approves does the layer run tasks 1 to 3 over just the new entities (not the whole home), merge validated additions into the feature spec, bump the feature set version, backfill features for the new columns, retrain in the background, and let the promotion gate decide whether the new model replaces the live one. The token cost estimate is shown before the user confirms.

This gives you exactly the flow you described: detect, ask, then (only on yes) analyze, retrain in the background, and auto compare, with transparency preserved and no silent spend.

---

## How this maps onto the existing code (so the proposal is implementable, not abstract)

- `ports.py` `LlmAdvisor`: add `propose_feature_spec(catalog, activities, whitelist) -> FeatureSpec` and `revise_feature_spec(spec, feedback) -> SpecDelta`. Keep the existing methods; the new ones supersede the unimplemented `propose_composites`.
- `openrouter_llm.py`: implement the three task prompts and the validation pipeline (extend the `validate_predicate` pattern to `validate_feature`).
- `features/registry.py`: today recipes are a closed registry keyed on Role. Add a spec driven builder so a feature can be defined by a `(transform, inputs, params, window)` tuple from the spec, not only by a hardcoded Recipe. The 13 existing recipes become the default whitelist entries (the conservative fallback tier), so nothing regresses.
- `features/pipeline.py`: `compute_features` already takes composites as data; generalize it to consume the full feature spec.
- `schemas.py`: add `FeatureSpec`, `FeatureDef`, `EntitySelection`, `InfoTier`.
- SQLite: store the active feature spec as a versioned settings blob (like `composites` today); store the per entity selections so the Sensors page can show role, tier, reliability and reason.
- Models page subpages (your transparency requirement): the per model stats (AUC, accuracy_confirmed vs accuracy_bootstrap, per class P/R/F1, confusion matrix, SHAP importances, evidence profile, and now the feature spec version and its diff from the previous model) all already exist in `metrics_json`; they just need dedicated subpages. Designed in Step 5.

This keeps everything behind the existing ports and degradation guarantees: no LLM at inference, heuristic floor when no key or no consent, propose plus human approve throughout, and the feature set versioning that prevents train serve skew.

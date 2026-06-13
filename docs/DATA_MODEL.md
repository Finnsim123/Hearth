# Hearth — Data Model

> Part of the [Hearth](../README.md) docs · design language in [DESIGN.md](DESIGN.md)

Two stores, strict separation of concerns:

- **InfluxDB** — everything that is a time series (raw states, features,
  predictions, labels-as-events, ops metrics).
- **SQLite** (volume-mounted, SQLAlchemy + Alembic migrations) — everything that
  is application state (bindings, taxonomy, rules, model registry, questions,
  connection settings).

## 1. InfluxDB buckets

| Bucket | Retention | Contents |
|---|---|---|
| `hearth_raw` | 180 d (configurable) | Mirrored entity states, 1 measurement per binding |
| `hearth_features` | 365 d | Materialized feature windows |
| `hearth_ml` | infinite | Predictions, labels, model metrics, heartbeats |

### 1.1 `hearth_raw`

One measurement per binding, uniform schema (fixes the prototype's `value` vs
`state` Flux collisions by never mixing types in one measurement):

```
measurement: raw_<binding_id>            e.g. raw_sofa_presence
tags:        entity_id, role, room, person
fields:      num (float) | str (string)  -- exactly one, decided by role
```

### 1.2 `hearth_features` (the feature store)

```
measurement: features
tags:        person, feature_set (vN), window ("30m")
fields:      one float per feature column        e.g. sofa_presence_frac=0.83
time:        window START (UTC)
```

Written by the window-builder job; read by inference (latest row) and training
(matrix over range). `feature_set` bumps on recipe changes; mixed-version
training is refused.

### 1.3 `hearth_ml`

```
predictions:  tags person, model_version       fields predicted(str), confidence,
                                               prob_<class>..., smoothed(str)
labels:       tags person, provenance(bootstrap|discovered|confirmed), source
              fields label(str), activity(str, optional sub), window_ts(float)
metrics:      tags person, model_version       fields accuracy_confirmed,
                                               accuracy_bootstrap, f1_<class>,
                                               auc_<class>, n_train, n_confirmed...
heartbeat:    fields alive=1                   (Grafana/UI alert: gap > 2× cadence)
```

## 2. SQLite schema (summary)

```
users           id, email, display_name, role(admin|member), password_hash
                (argon2id), person_id?, disabled, failed_logins, backoff_until
sessions        id, user_id, token_sha256, created_at, last_seen_at, expires_at
                -- server-side, revocable; cookie holds the unhashed id
connections     id, kind(ha|influx|mqtt|llm), url, token_encrypted (Fernet),
                status, last_ok_at, options_json(llm: model, max_cost_per_call;
                influx: org, mode bundled|external)
api_tokens      id, name, token_sha256, scope(integration|readonly), created_at,
                last_used_at, revoked_at        -- consumed by the HA integration
persons         id, name, ha_person_entity, notify_service, ask_budget_per_day,
                quiet_hours, enabled
bindings        id, entity_id, role, room, person_id?, options_json, enabled,
                created_at        -- the generalization mechanism (ADR-8)
activities      id, slug, name, icon, color, parent_id?  -- two-level taxonomy
rules           id, activity_id, person_id?, predicate_json, priority, enabled,
                origin(user|discovered), created_from_cluster_id?
questions       id, person_id, window_ts, predicted, confidence, channel,
                actions_json, status(open|answered|expired), answer, answered_at
models          id, person_id, version, algo, feature_set, path, trained_at,
                train_window_json, label_counts_json, metrics_json,
                promoted(bool), promoted_at
training_runs   id, model_id?, status, log_path, started_at, finished_at, error
clusters        id, run_at, algo, params_json, signature_json, n_windows,
                example_windows_json, status(new|named|dismissed|merged),
                named_activity_id?
settings        key, value_json                  -- misc UI settings
```

Notes: every secret column follows docs/SECURITY.md — passwords argon2id,
session/API tokens stored as SHA-256, third-party tokens Fernet-encrypted with
a key derived from `HEARTH_SECRET`; all via `hearth/security.py`, nowhere else.
`predicate_json` is a small AST: `{all: [{feat: "kitchen_presence_frac", op: ">",
value: 0.3}, {feat: "stove_fumes_any", op: "==", value: 1}]}` — renderable and
editable in the UI, evaluable in pandas, no eval().

`models.algo` is one of `random_forest | gradient_boosting | logistic |
embedding`. New `settings` keys added by the AI layer: `feature_spec` (the active
executable feature spec), `feature.power_mode` (conservative|full),
`llm.share_stats` (yes|no consent), `training.config` / `asking.policy` /
`output.policy` (the behaviour knobs, defaults = the historical constants),
`discovery.pending` (sensors awaiting approval) and `discovery.integrate`
(re-analysis/retrain progress for the buddy).

## 3. Roles and their feature recipes (initial set)

| Role | Example entities | Features per window (prefix = binding name) |
|---|---|---|
| `presence` | mmWave/PIR per room | `_frac`, `_any`, `_transitions` |
| `bed` | load cell / FSR voltage | `_mean`, `_max`, `_occupied` (threshold opt), sentinel −1 |
| `power` | smart plugs (W) | `_on` (>thr), `_max_w`, `_delta_kwh` |
| `light` | light/group | `_on_last`, `_on_frac` |
| `media` | media_player, client counts | `_playing`, `_paused`, `_active` |
| `env` | CO₂/PM2.5/temp/humidity | `_mean`, `_delta`, `_max` |
| `person` | person.* / device_tracker | `_home_last`, zone-category one-hots |
| `focus` | phone focus/DND binary | `_on_last` |
| `alarm_time` | input_datetime | `minutes_until`, `imminent` |
| `door` | contact sensor | `_opened_any`, `_open_count` |
| `steps`/`battery` | phone telemetry | `_delta` |
| `custom` | anything numeric | `_mean`, `_max`, `_delta` |

Cross-binding composites (declared in recipe config, not code): lights-off+in-bed,
media+sofa, fumes+kitchen-presence, pre-alarm indicator, partner-context flags,
lag features (window t−1, t−2 of selected columns).

Beyond these fixed recipes, the AI feature architect (ARCH §6b) can add features
from a safe transform whitelist (`features/transforms.py`), keyed to each
entity's information tier, executed by the same deterministic builder and hashed
into the feature-set version. The recipes above are the conservative default and
the only thing that runs with no LLM key.

## 4. Entity inventory (onboarding artifact)

One catalog record per entity, built automatically (ARCH §6b), downloadable in
the wizard. This is what the LLM reads (`onboarding/inventory.py`):

```json
{
  "entity_id": "binary_sensor.presence_sensor_sofa",
  "metadata": {
    "domain": "binary_sensor",
    "friendly_name": "Sofa presence",
    "device_class": "occupancy",
    "state_class": null,
    "unit_of_measurement": null,
    "area": "Living room",
    "device": {"manufacturer": "Aqara", "model": "FP2"},
    "entity_category": null,
    "disabled": false,
    "hidden": false
  },
  "stats": {
    "window_days": 14,
    "value_type": "boolean",
    "distinct_values": 2,
    "top_states": [{"value": "on", "frac": 0.31}, {"value": "off", "frac": 0.69}],
    "numeric": null,
    "changes_per_day": 38.2,
    "median_seconds_between_changes": 220.0,
    "active_hours_hist": [0,0,0,0,0,0,0.02,0.05,"..."],
    "longest_gap_hours": 7.5,
    "flatline_frac": 0.0,
    "last_changed_age_hours": 0.3
  },
  "samples": [{"ts_local": "2026-06-12T21:30:00+02:00", "state": "on"}],
  "current_binding": {"role": "presence", "name": "sofa", "enabled": true,
                      "model_importance": 0.14}
}
```

For numeric entities `stats.numeric` carries `{min, p05, median, p95, max,
monotonic_increasing_frac}`; `monotonic_increasing_frac ≈ 1.0` plus
`state_class: total_increasing` is the cumulative-counter signature, and
`flatline_frac ≈ 1.0` is the stuck-sensor signature — both drive the
information tier and the reliability flag (suspect / unusable).

`stats` and `samples` are populated only when (a) a history source exists and
(b) the user **consented** to sharing aggregate stats (an explicit yes/no, the
`llm.share_stats` setting). With consent off, or no history, the LLM works from
`metadata` alone (`stats`/`samples` are null) and the reliability flag falls
back to a deterministic pass over basic signals. Privacy: metadata, aggregate
statistics, and at most a handful of recent sample states are the ONLY things
sent to an LLM; raw time series never leave the stack. `current_binding` is
present only on a re-analysis, so the LLM revises rather than restarts.

## 5. How much history is needed? (defaults, surfaced in the wizard)

| Purpose | Minimum | Comfortable | Notes |
|---|---|---|---|
| Inventory stats for LLM/heuristics | 3 d | 14–30 d | metadata-only works with 0 d |
| Discovery clustering (Pattern cards) | 3 d | 14–28 d | cards improve sharply with weekend coverage |
| First model, 3 top-level classes | 7 d | 14 d | ≈336 windows/person/week |
| Sub-activities (cooking, movie, …) | — | 21–42 d | rare-class bound: aim ≥30 windows *per class*; cooking ≈1×/day → ~a month |
| Rolling training window (steady state) | — | 42–56 d | older data ages out (drift, P5) |
| HEPA-style pretraining (Phase 4) | 30 d | 90 d+ | unlabeled — history import makes this free |

The binding criterion is **windows per class**, not calendar time — the
Models page shows per-class counts and marks classes "untrainable" below the
threshold rather than silently training a bad model. **History import**
(existing HA→Influx bucket or HA recorder) short-circuits all of this: a home
with months of recorded sensor data can reach "comfortable" on day 0.

## 6. Identity & time conventions

Window identity is `(person, window_start_utc, window_size)` everywhere — labels
join predictions join features on exactly this key (floored to the window grid).
All storage UTC; `hour_of_day`/weekday features computed in the home's configured
timezone. Feedback answers attach to the question's stored `window_ts`, never to
"now" (fixes the prototype's stale-sensor-attribute race).

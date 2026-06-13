# Hearth Architecture Audit (Step 1)

Status: draft for your confirmation, June 2026.
Scope: read only. Nothing in the repo was modified. This document maps the existing implementation onto your six stage pipeline (raw input, data analysis via LLM, feature engineering, model training, output, feedback loop), records what is hardcoded versus configurable per stage, flags deviations from a clean modular design, names the single biggest reliability risk, and ends with a current versus target modularity table.

## 0. What I read

Backend domain code (the product): `ports.py`, `schemas.py`, `config.py`, `scheduler.py`; `domain/onboarding/advisor.py`; `adapters/openrouter_llm.py`; `domain/features/{pipeline,registry}.py`; `domain/training/{trainer,estimators,evaluate}.py`; `domain/inference/{predictor,smoothing}.py`; `domain/fasttrack.py`; `domain/flow.py`. Docs: `ARCHITECTURE.md`, `DATA_MODEL.md`, `METHODOLOGY.md`, `RESEARCH.md`, `README.md`. Frontend: `onboarding/Wizard.tsx` (10 step wizard, confirmed). I did not exhaustively read every adapter, test, or page, but I read enough of each layer to map the data flow end to end and to verify the docs against the code rather than trusting the docs alone.

Headline finding: the docs are unusually accurate to the code. Where I found drift between doc claims and implementation I call it out explicitly (section 3).

## 1. The system in one paragraph

Hearth is a modular monolith with a hexagonal core (`domain/` is pure logic, `adapters/` hold all I/O behind `domain/ports.py` Protocols, `main.py` wires them, `api/` is thin). Sensor states arrive over the HA WebSocket, are written to InfluxDB `hearth_raw` keyed on a Binding (entity to Role mapping stored in SQLite), materialized into 30 minute feature windows in `hearth_features` by a deterministic role recipe engine, classified per person by a hierarchical Random Forest (LCPN: a coarse root model plus one child model per parent activity), and the predictions flow out to HA as entities. Uncertainty drives questions back to the user; confirmed answers plus named clusters plus rule labels feed a weekly retrain gated by a confidence interval test. An optional LLM advisor (BYO OpenRouter key) runs once at onboarding to map entity names to roles, draft labeling rules, propose a taxonomy, and optionally weak label history.

## 2. Stage by stage mapping

### Stage 1: Raw input

What it does: subscribes to HA `state_changed` over WebSocket for exactly the bound entities, writes each to `hearth_raw` (one measurement per binding, `num` xor `str` field decided by role), gap fills on reconnect via HA REST history, and optionally backfills an existing HA to Influx bucket. Entity selection is the Binding table, not HA config.

Where: `adapters/ha_websocket.py`, `adapters/ha_rest.py`, `adapters/influx_import.py`, `adapters/influx_store.py`, `domain/ingest.py`. Orchestration in `scheduler.py` (`_ingest_forever`, long running task). Bindings created in `domain/onboarding/{inventory,advisor}.py` and the wizard.

Configurable: which entities are bound, each binding's role, room, person, enabled flag, and `options` (role specific thresholds, evidence tier override, `pet_immune`, etc.). Source bucket and `import.max_days` cap for fast track.

Hardcoded: the 1 minute resample grid; the `hearth_raw` schema; per role forward fill limits, absence sentinels, slow sensor flags and lookback windows (all in `features/registry.py`, so they are data on a Recipe object but not user editable); the 30 second ingest poll fallback.

Deviation from clean modular design: minor. Ingest is well isolated behind `EventSource`. The one smell is that ingest cadence and resample grid are compile time constants rather than settings, so a user cannot trade ingest volume against responsiveness.

### Stage 2: Data analysis (the LLM layer)

This is the stage you care most about, and it is the least built out relative to your stated aim. What exists today:

What it does: at onboarding, an entity inventory is exported from three HA calls (`/api/states`, entity registry, area registry) plus aggregate stats per entity over 7 to 30 days (distinct values, changes per day, active hours histogram, value range, percent missing). The inventory schema is defined (`DATA_MODEL.md` section 4). The LLM advisor then proposes: entity to Role bindings (`propose_bindings`), person to home/away entity matches (`match_person_entities`), canonical room names (`propose_room_canon`), a starter activity taxonomy (`propose_taxonomy`), draft labeling rules as validated JSON predicate ASTs (`propose_rules`), optional weak labels over batched window summaries (`annotate_windows`), and cluster naming hints (`suggest_cluster_name`). A no key heuristic floor (`onboarding/advisor.py`: `suggest_role`, `heuristic_bindings`, `is_bindable`) covers the same screens by name pattern, device_class, unit and domain rules. All LLM output is schema validated item by item and degrades to the heuristic floor on any failure. Everything is proposed, never applied: the user approves each wizard screen.

Where: `domain/onboarding/advisor.py` (heuristics plus the orchestration `suggest_setup`), `adapters/openrouter_llm.py` (the actual prompts and validation), `domain/ports.py` (`LlmAdvisor` Protocol). Model defaults to `openai/gpt-4o-mini`, temperature 0, chunked at 300 entities per call.

Configurable: LLM model (wizard plus Settings), whether the LLM runs at all (BYO key), and downstream every proposal is editable by the user.

Hardcoded: the prompts themselves; the safe predicate grammar and operator whitelist (`validate_predicate`, `_OPS`); the role to domain physics map (`ROLE_DOMAINS`, `_NEVER_DOMAINS`); the diagnostics blocklist regex.

Deviations and gaps (these directly shape Steps 2 to 5, so I am specific):

1. The LLM does not do feature engineering. It selects a Role per entity; the Role then deterministically fixes the features via the recipe registry. The "what to feed the model" decision is therefore made by a fixed lookup table of 13 roles, not by the LLM. Your aim ("the LLM interprets a heterogeneous set of entities and decides what to feed the model") is only half met: the LLM decides relevance and role, not the feature transforms.

2. `propose_composites` is declared in the `LlmAdvisor` Protocol (`ports.py`) but is not implemented in `OpenRouterAdvisor` and is not called by `suggest_setup`. So the documented claim that the LLM "proposes candidate cross sensor features generously" is currently unimplemented. Composites exist only as a settings stored JSON AST that a human or the heuristic path can populate. Judgment: this is the single largest doc to code gap, and it is exactly the seam Step 3 needs.

3. The prompts do not inject the per entity stats. `propose_bindings` sends only `entity_id | device_class | unit | friendly_name` per line. The inventory carries `changes_per_day`, `pct_missing`, `value_range`, `active_hours_hist`, but none of it reaches the model. So the LLM reasons purely from names and metadata, never from observed behavior. Judgment: this is why the system cannot currently flag unreliable sensors. The data exists; it is simply not in the prompt.

4. There is no data quality or sensor reliability assessment anywhere. Nothing predicts what a sensor should do and flags deviation. Empty columns are pruned in fast track (`fasttrack.py`: sensors with zero imported points are disabled), and the evidence tier system caps confidence on weakly evidenced predictions at inference (`features/evidence.py`), but neither is a design time reliability audit of the kind you describe. This is a clean greenfield for Step 3.

### Stage 3: Feature engineering

What it does: the window builder reads raw, resamples to 1 minute with role aware forward fill, extracts per binding features via the role recipe (for example presence yields `_frac`, `_any`, `_transitions`), applies cross binding composites (a JSON AST evaluated with no `eval`), adds lag columns, imputes with role absence semantics (minus 1 means sensor absent, 0 means no event), and persists to `hearth_features` tagged with a `feature_set` version. The exact same code path feeds training matrices and live inference rows, so there is no train serve skew (ADR-7). It also adds cross cutting event dynamics (`evt_count`, `evt_active_sensors`, `evt_dominant_share`, `evt_idle_minutes`) and a coarse time encoding (4 bucket part of day plus weekend flag, deliberately not raw hour, to avoid the clock crutch failure).

Where: `domain/features/pipeline.py` (orchestration plus pure functions), `registry.py` (one Recipe per Role: ffill limit, absence value, slow sensor flag, suffixes, per role lookback `window_min`), `extractors.py`, `composites.py`, `evidence.py`, `person_scope.py`.

Configurable: composites (settings JSON), lag_features (settings), time_granularity (coarse, full, none), per binding options (thresholds, imminent window). `feature_set_version()` hashes the recipe set plus composites plus time granularity, so any recipe change bumps the version and refuses mixed version training.

Hardcoded: the 30 minute window (a `Literal["30m"]` in schemas with a comment that the field exists to widen later); stride (5 minute inference, 30 minute training); the recipe suffix set per role; per role `window_min` lookbacks (15 to 180 minutes); the event dynamics feature set; the 240 minute idle cap; evidence tier assignment per role.

Deviation from clean modular design: the recipe registry is the right abstraction (features keyed on Role, not entity name, ADR-8), but it is a closed set defined in module level `register(...)` calls. Adding or editing a recipe is a code change, not a configuration change. Window length being a `Literal` rather than a per feature_set setting is the most consequential rigidity here, because windowing dominates HAR accuracy (this is central to Step 4).

### Stage 4: Model training

What it does: one run per person, scheduled Sunday 03:00 plus an on demand "Train now" plus a cold start accelerator that trains as soon as enough windows exist. Reads a single feature_set matrix, generates bootstrap labels from rules, overlays confirmed and discovered and LLM labels by trust, projects to coarse states for the root model and fits one child model per parent with enough fine labels (LCPN hierarchy). Temporal split (last 7 days held out, never shuffled), recency weighted samples (21 day half life), per class isotonic calibration fitted after honest evaluation, top 15 SHAP importances and an evidence profile recorded, then a promotion gate (Wilson interval overlap on confirmed accuracy) decides whether the new model replaces the active one. Monthly hyperparameter tuning via RandomizedSearchCV with TimeSeriesSplit (never shuffled), scoring f1_macro.

Where: `domain/training/trainer.py`, `estimators.py` (only `RandomForestEstimator` implemented behind the `Estimator` port), `evaluate.py` (Wilson interval, PSI drift, per class P/R/F1/AUC, confusion). Scheduling in `scheduler.py`.

Configurable: train window weeks (call argument, defaults 8), force flag. Hyperparameters are auto tuned and cached per person, not user set.

Hardcoded: `MIN_TRAIN_WINDOWS=100`, `VAL_DAYS=7`, `RECENCY_HALF_LIFE_DAYS=21`, `TUNE_MIN_WINDOWS=500`, `TUNE_EVERY_DAYS=30`, the parameter search space, `class_weight="balanced"`, the promotion tolerance (2 points), `MIN_CHILD_WINDOWS`, the Sunday 03:00 and 30 minute cadences.

Deviation from clean modular design: the `Estimator` port is clean and the trainer mostly programs against it, but two leaks exist. First, `_fit_node` reaches into `est.model.feature_importances_` and constructs `RandomForestEstimator` by name, so swapping in a gradient boosted estimator would need those call sites touched. Second, calibration and `sample_weight` are assumed on the estimator (`hasattr(est, "calibrate")`, `est.fit(..., sample_weight=...)`) rather than expressed in the port. Judgment: the port is 80 percent of the way to true swappability; the importance extraction and the fit signature are the remaining couplings.

### Stage 5: Output

What it does: newest feature windows become predictions. With a promoted model: probabilities, top 3 SHAP explanation, a learned transition forward filter (a Laplace smoothed per household transition matrix mixed 85/15 with uniform), hysteresis smoothing (publish a switch only on k consecutive wins or a decisive margin), and an evidence cap (if direct tier SHAP share is below 0.25 while confidence is above 0.70, confidence is capped to 0.70 so the prediction asks instead of asserts). Without a model, it falls back to bootstrap rules at fixed 0.55 confidence so day one homes still get a correctable ribbon. Two serving lanes: a 5 minute grid lane for the dashboard and an event driven realtime lane that fires `hearth_activity_changed` on the HA bus within about 10 seconds of a sensor change. Predictions leave via a custom HA integration (primary), MQTT discovery, or REST (all behind the `EntityPublisher` port).

Where: `domain/inference/predictor.py`, `smoothing.py`, `realtime.py`; `adapters/mqtt_publisher.py`, `ha_rest.py`; `custom_components/hearth/` (the HACS integration).

Configurable: ask threshold, evidence cap and weak share are constants today; the integration host and token are user set.

Hardcoded: `RULES_CONFIDENCE=0.55`, `WEAK_CONFIDENCE_CAP=0.70`, `WEAK_DIRECT_SHARE=0.25`, smoothing `k=2` and `margin=0.25`, transition `UNIFORM_MIX=0.15`, the 5 minute grid cadence, the realtime 3 second debounce.

Deviation from clean modular design: small. The post processing chain (transition filter, then hierarchy descent, then evidence cap, then hysteresis) is hand sequenced inside `predict_person` rather than expressed as composable post processors, which makes it hard to reorder or expose individual stages as UI levers.

### Stage 6: Feedback loop

What it does: uncertainty sampling plus margin sampling (ask when top 2 gap is below 0.25) plus about 7 percent random asks on confident windows, budgeted per person, quiet hours respected, never for silent activities, keyed on action identifiers plus server side question rows (because iOS drops notification tags). Answers become confirmed labels. A nightly or weekly HDBSCAN discovery job clusters unexplained windows into Pattern cards the user names, which labels weeks of history at once and drafts a rule. Weekly retrain closes the loop. The headline accuracy metric is computed on confirmed labels only, with a Wilson interval.

Where: `domain/labeling/{active,rules,merge,bulk,taxonomy,phrasing,starter_rules}.py`, `domain/discovery/clustering.py`, `domain/milestones.py`, the Inbox page, the HA integration's event listener.

Configurable: `ask_budget_per_day`, `quiet_hours`, per person enablement, opt out switch exposed in HA.

Hardcoded: the 0.25 margin, the 7 percent exploration rate, the ask threshold, discovery cadence (Saturday 04:00), question expiry (6 hours), clustering parameters.

Deviation from clean modular design: the feedback loop is well separated but its many thresholds are scattered constants across `active.py`, `predictor.py` and `trainer.py` rather than collected in one policy object, which is a problem for your "expose as UI levers" goal because the levers do not currently live in one place.

## 3. The single biggest reliability risk

The promotion gate is circular at cold start, which is the exact prototype failure mode re entering through the gate rather than through the headline metric.

Evidence: `trainer.promotion_gate` promotes a new model when its confirmed accuracy confidence interval does not fall materially below the current model's. But it explicitly falls back: "No confirmed labels yet, fall back to bootstrap agreement comparison" (`a, b = new.metrics.get("accuracy_bootstrap"), ...; return a >= b - 0.02`). On a fresh install and on every fast track home (`fasttrack.py` calls `train_person(..., force=True)`), there are zero confirmed labels at first training. With `force=True` the gate is bypassed entirely; without it, the gate compares bootstrap agreement, which measures how well the model reproduces the rules that generated its own training labels. The headline accuracy metric is honest by construction (it is null until confirmed labels exist), but the decision to put a model live is made by rule agreement. So the first model a user ever sees, including the one trained during the setup wizard "predictions within minutes" path, is promoted on a circular signal. The user is told the model is ready before any non circular evidence exists that it is.

Why this is the top risk and not the others: it sits precisely at the moment of maximum user trust (end of onboarding), it is invisible (the metric that is shown is correctly null or bootstrap labeled, so nothing looks wrong), and it directly undermines the product's central honesty claim. Every other risk I found (scattered thresholds, the closed recipe set, the estimator port leaks) is a maintainability or flexibility issue, not a correctness one.

Judgment on the fix direction (for later, not now): the cold start gate should require either a minimum count of confirmed labels before claiming "ready", or an explicit, clearly labeled "provisional, unvalidated" model state in the UI, or a held out time block evaluated against discovered (cluster named) labels as a non circular interim signal. This interacts with Step 4 (validation strategy) and Step 5 (UI honesty), so I am flagging it, not designing it here.

Runner up risks, noted but secondary: (a) the LLM weak labels and bootstrap rules both feed training and both can encode the same human assumption, so they are not independent error sources even though they are different provenances; (b) windowing is fixed at 30 minutes and is not a lever, despite the research notes acknowledging segmentation (P4) as where models err most.

## 4. Current versus target modularity

Target modularity means: the stage has one explicit input contract and one output contract; its behavior shaping parameters are data (settings or a versioned config), not code constants; and the stage can be swapped or re configured without editing another stage.

| Stage | Current modularity | Target modularity | Main gap to close |
|---|---|---|---|
| 1 Raw input | High. Behind `EventSource` and `TimeSeriesStore` ports; entity selection is data (Bindings). | High. | Expose ingest cadence and resample grid as settings. |
| 2 Data analysis (LLM) | Low to medium. `LlmAdvisor` port is clean and degrades to heuristics, but prompts are hardcoded, stats are not injected, `propose_composites` is unimplemented, and there is no reliability audit. | High. LLM emits a validated, executable feature specification plus a sensor reliability report; prompts and model are config. | The whole of Step 3. This is the least modular, highest leverage stage. |
| 3 Feature engineering | Medium. Pure functions, versioned feature sets, no train serve skew, composites are data. But recipes are a closed code defined set and window length is a `Literal`. | High. Recipes and windowing are a versioned config the LLM and the user can extend within a safe whitelist. | Make windowing a first class lever; let the feature spec from Stage 2 drive recipe selection. |
| 4 Model training | Medium. `Estimator` port exists but only RF is implemented and the trainer leaks RF specifics (`feature_importances_`, fit signature). Many constants. | High. Estimator fully swappable (GBT, logistic, MLP) behind a richer port; training policy is config. | Lift importance, calibration and sample weight into the port; collect constants into a training policy object. |
| 5 Output | Medium to high. `EntityPublisher` port is clean; post processing is a hand sequenced chain of constants. | High. Post processors composable and individually toggleable; thresholds are settings. | Express transition filter, evidence cap, smoothing as ordered, configurable steps. |
| 6 Feedback loop | Medium. Well separated mechanisms, but the asking and exploration thresholds are scattered constants across three modules. | High. One asking and retrain policy object, all thresholds as settings, surfaced as UI levers. | Centralize the policy; this is also a prerequisite for the levers UI in Step 4 and 5. |

## 5. My understanding of your end goal (please confirm or correct)

You want an X step setup wizard that connects to HA and InfluxDB, runs a full automatic data analysis (driven by the LLM, including sensor relevance, feature definition, and data quality or reliability assessment), sets up and trains the model once, and from then on uses the LLM only for maintenance and insight, never in the per prediction inference loop (to keep token cost at "spend once" rather than "pay per prediction"). After setup the UI is insight, settings, and the feedback loop. The model runs locally for free or compute only cost. The current repo already nails the spend once principle (the LLM is onboarding only; steady state inference is 100 percent local RF), so your architecture instinct is already in the code; the work is to deepen the design time LLM role from "name to role mapping" into "feature architect plus reliability auditor" and to make every stage's levers into data the wizard and UI can drive.

## 6. Three questions before I design on top of this

1. Confirm the LLM stays strictly design time and maintenance time (never per prediction), and that "maintenance" may include scheduled re analysis (for example a monthly LLM pass to re evaluate feature relevance against fresh importance and drift stats). Is a recurring maintenance pass in scope, or is it strictly one shot at setup plus on demand?

2. The current feature layer is a fixed registry of 13 role recipes. For Step 3, do you want the LLM to (a) keep selecting from that fixed recipe set (lowest risk, the LLM picks roles and composites only), or (b) propose new parameterized transforms within a safe whitelist that the deterministic builder then executes (the CAAFE style executable feature spec, higher power, the direction your prompt describes)? My read is you want (b); I will design (b) with (a) as the conservative fallback unless you say otherwise.

3. For the reliability audit, confirm the LLM may receive the per entity aggregate stats that already exist in the inventory (changes per day, percent missing, value range, active hours histogram). These are not raw history and fit the existing privacy contract, but they are not currently sent. Step 3's reliability flagging depends on sending them.

I will pause here. On your confirmation (and any corrections to section 5) I will proceed to Step 2 (the research brief).

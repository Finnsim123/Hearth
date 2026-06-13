# Gap Analysis: Design versus Current Repo

Status: gap analysis, June 2026. Read only on the repo. This enumerates everything that still needs to be built to get from the current codebase to the design in the five companion documents (architecture_audit, llm_for_ml_input_landscape, llm_layer_design, model_levers, pipeline_modularity_spec).

How to read this:
- Status is one of EXISTS (built, do not rebuild), PARTIAL (some of it exists, needs extension or change), or MISSING (not in the repo).
- Effort is a rough size, S (hours), M (days), L (week or more), labeled as a judgment because I have read the code but not built against it.
- Dependencies name what must exist first.
- Everything traces to a specific design section so you can jump back.

The headline: Hearth's runtime spine (ingest, feature store, training, inference, output, feedback, the wizard, the Models page) is already built and mostly modular. The gaps cluster in three places: the cold start honesty fix (small, high value), the feature specification mechanism that turns the LLM from a role mapper into a feature architect (the core new build), and the transparency plus approval UI around them. Almost nothing needs to be thrown away; most of the work is adding a spec layer on top of what exists and lifting constants into config.

---

## Part 1: What already exists (do not rebuild)

Stated explicitly so effort is not wasted re implementing working code.

- Hexagonal architecture: `domain/ports.py` Protocols, adapters behind them, `main.py` composition root. EXISTS.
- Stage 1 raw input: HA WebSocket ingest, REST gap fill, Influx backfill importer, `hearth_raw` store, the Binding (entity to role) system, SQLite app state. EXISTS.
- Stage 2 today: entity inventory export (metadata plus basic aggregate stats), heuristic suggester, LLM advisor for bindings, person matching, room canonicalization, taxonomy, rules, weak window annotation, cluster naming. The propose plus validate plus human approve discipline and the heuristic floor. EXISTS.
- Stage 3 features: 1 minute resample, role aware forward fill, 13 role recipes, composites as a JSON AST, lag features, semantic imputation, event dynamics features, coarse time encoding, persisted versioned feature store, `feature_set_version()` hashing that forces clean retrains. EXISTS.
- Evidence tiers (`features/evidence.py`) and the inference confidence cap. EXISTS (note: this is the runtime trust axis, distinct from the new design time information tier).
- Stage 4 training: per person models, LCPN hierarchy (root plus child per parent), temporal holdout split, recency weighting, per class isotonic calibration, hyperparameter tuning with TimeSeriesSplit (never shuffled), Wilson interval promotion gate, rollback (backend), the `Estimator` port. EXISTS.
- Stage 4 evaluation: confirmed only headline accuracy with Wilson CI, bootstrap agreement reported separately, per class P/R/F1, macro AUC, confusion matrix, PSI drift function, feature importances, evidence profile. EXISTS.
- Stage 5 output: predictor, learned transition forward filter, hysteresis smoothing, grid lane plus event driven realtime lane, rules fallback for cold start, HA custom integration plus MQTT plus REST behind `EntityPublisher`. EXISTS.
- Stage 6 feedback: uncertainty plus margin plus epsilon asking, server side question rows keyed on action ids, dynamic phrasing, inbox, bulk range labeling, HDBSCAN discovery with pattern cards. EXISTS.
- Fast track orchestration, scheduler jobs, 10 step wizard, Models page (rich metrics, confusion, evidence bar, importances, promote, train now), Methodology page, flow map, auth and tokens. EXISTS.

This is a large, working product. The gaps below are additions and changes, not a rebuild.

---

## Part 2: The gaps, grouped

### Group A: Cold start honesty (the Step 1 biggest risk)

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| A1. `min_confirmed_before_validated` gate | MISSING | model_levers, audit s3 | S | Below the threshold, do not claim the model is validated; the gate today falls back to bootstrap agreement (circular) or is bypassed by `force=True` in fast track. |
| A2. Provisional versus validated model state | MISSING | modularity spec Stage 4, model_levers | S | A badge on the Models page and the wizard step 9. The data to compute it (n_confirmed) already exists in `metrics`. |
| A3. Non circular cold start signal (optional) | MISSING | audit s3 | M | Evaluate the first model against discovered (cluster named) labels as an interim non circular check, or simply hold "validated" until confirmed labels arrive. Decision needed (see Part 4). |

Why first: this is the highest value to effort ratio in the whole plan. It is small, it fixes the single biggest reliability risk, and it does not depend on any of the larger builds. It also changes a user facing promise (do not tell someone a model is ready when the only evidence is circular), so it should land before the system starts onboarding more homes.

### Group B: Cross cutting configuration (prerequisite for levers)

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| B1. Unified versioned pipeline config object | MISSING | modularity spec summary | M | Behavior is split between `config.py` constants, module constants (`trainer.py`, `pipeline.py`, `smoothing.py`), and SQLite settings. Consolidate into one per instance (and per person where noted) config blob. |
| B2. Training config object (collect constants) | MISSING | model_levers G3, modularity Stage 4 | S | MIN_TRAIN_WINDOWS, VAL_DAYS, RECENCY_HALF_LIFE_DAYS, tune floors, gate margin become config fields. |
| B3. Asking policy object (collect constants) | MISSING | model_levers G6, modularity Stage 6 | S | Ask budget, quiet hours, margin, epsilon exploration, thresholds scattered across `active.py` and `predictor.py` into one object. |
| B4. Post processing chain as ordered toggleable steps | PARTIAL | model_levers G6, modularity Stage 5 | M | Today hand sequenced in `predict_person`. Express transition filter, hierarchy descent, evidence cap, hysteresis as an explicit ordered pipeline. |

Why early: every Step 4 lever and the Settings UI depend on these values being data, not constants. Doing this before the UI work means the UI binds to a stable config surface.

### Group C: Spec driven feature engineering (the core enabler)

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| C1. Feature spec schema (`hearth.feature_spec.v1`) | MISSING | llm_layer_design d | S | The output contract: selections plus executable feature defs. Add to `schemas.py`. |
| C2. Transform whitelist (the safe vocabulary) | MISSING | llm_layer_design d | M | The ~22 illustrative transforms, each with valid tiers, param schema, input kind. The 13 existing recipes become the conservative default subset (no regression). |
| C3. Spec driven feature builder | MISSING | llm_layer_design d, modularity Stage 3 | L | Generalize `features/pipeline.py` and `registry.py` so a feature is defined by a (transform, inputs, params, window) tuple from the spec, executed by a pure function. This is the single substantive new mechanism. |
| C4. `validate_feature` pipeline (8 steps) | MISSING | llm_layer_design d | M | Extends the existing `validate_predicate` pattern: schema, whitelist, tier compatibility, input existence and kind, param bounds, name uniqueness, reliability gate, budget cap. |
| C5. Feature spec hashed into `feature_set_version` | PARTIAL | llm_layer_design d, modularity Stage 3 | S | `feature_set_version()` already hashes composites and time granularity; extend it to hash the full spec so any spec change forces a clean retrain (ADR-7 preserved). |
| C6. Window length as a setting (15/30/60) | PARTIAL | model_levers G2, modularity Stage 3 | M | Today a `Literal["30m"]`. Plumb through the pipeline, the window grid, the feature set version, and the schema. First class HAR lever. |
| C7. Feature power mode setting (conservative/full) | MISSING | llm_layer_design intro, modularity Stage 2 | S | Switches the active whitelist contents between the recipe only fallback and the full parameterized set. Same mechanism, different contents. |

Why this is the backbone: C3 is what lets the LLM (Group D) actually define features rather than just pick roles. Build the spec, whitelist, builder, and validator first as a deterministic capability (a human or the heuristic can author a spec), then point the LLM at it. This sequencing means the feature layer is testable without any LLM, and the LLM becomes one more author of a validated artifact.

### Group D: LLM feature architect and reliability auditor

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| D1. Entity catalog v1 extension | PARTIAL | llm_layer_design a | M | Add state_class, entity_category, numeric percentiles, monotonic_increasing_frac, longest_gap_hours, flatline_frac, last_changed_age_hours, samples, current_binding.model_importance. The basic stats exist; these are the reliability and counter detection fields. |
| D2. Information tier taxonomy (T0 to T5) | MISSING | llm_layer_design b | S | New typed classification, distinct from evidence tiers. Assigned by the LLM, deterministically validated (e.g. T4 requires total_increasing). |
| D3. Send aggregate stats to the prompt | MISSING | llm_layer_design c, e | S | Prompts today send metadata only. Inject the stats block, gated by consent. |
| D4. Stats consent yes/no toggle plus implications | MISSING | llm_layer_design e, your requirement | S | Forced choice in the wizard, editable in Settings. The layer must work degraded when no. |
| D5. `propose_feature_spec` port method plus adapter plus 3 prompts | MISSING | llm_layer_design c, d | L | Selection, per entity feature, cross entity composite prompts; supersedes the unimplemented `propose_composites`. |
| D6. Reliability auditor (ok/suspect/unusable) | MISSING | llm_layer_design a, b, c | M | Uses the new stats fields; flags stuck, mostly missing, or behaving unlike its device_class implies. Surfaces in the feature spec and the UI. |
| D7. Stronger default model for the architect task | PARTIAL | llm_layer_design c | S | The model is already user selectable; set a capable default for the architect prompts, keep gpt-4o-mini for cheap tasks. |
| D8. Cost estimate shown before any LLM run | MISSING | llm_layer_design e, your requirement | S | Token usage is logged after the fact today; estimate and show before the user confirms. |

Why after Group C: the LLM emits a feature spec, so the spec, whitelist, builder, and validator (C1 to C4) must exist first. D5 is the largest single new piece of LLM work.

### Group E: Feedback loop and maintenance (your new-sensor flow)

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| E1. Discriminative statistics per confused pair | MISSING | llm_layer_design f | M | For each confused activity pair (read from the existing confusion matrix), compute which features separate them (effect size, or reuse the PSI machinery). The ZARA idea, at design time. |
| E2. `revise_feature_spec` port method plus revision prompt | MISSING | llm_layer_design f | M | Sends the model feedback summary (confusion, importances, evidence profile, discriminative stats) and gets add/drop deltas. |
| E3. Feedback loop orchestration plus stopping criterion | MISSING | llm_layer_design f | M | Apply delta, retrain, compare via the existing promotion gate; stop on no improvement, round cap, diminishing confusion, or insufficient labels. Reuses `promotion_gate`. |
| E4. New entity discovery: detect then ASK (not auto add) | PARTIAL, needs behavior change | llm_layer_design f, modularity Stage 2, your requirement | M | Today `inventory_sync` auto adds and enables new bindable entities daily with no LLM and no prompt. The design requires: detect new entities, raise a UI prompt and notification, and do nothing else until approved. This is a change to existing behavior, not just new code. |
| E5. User approved scoped integration | MISSING | llm_layer_design f, your requirement | M | On approval, run the analysis over only the new entities, merge into the spec, bump the feature set version, backfill the new columns, retrain in the background, let the gate decide. Show the cost estimate first. |

Why E4 matters specifically: your stated requirement ("when I add a sensor to test something it should not straight away burn tokens and train models") is currently violated in spirit. `inventory_sync` silently adds and enables new sensors, which means the next scheduled train includes them with no approval. Today it does not call the LLM (use_llm is False in the scheduler) so no tokens burn yet, but the moment the LLM maintenance pass is wired in, the auto add path would burn tokens without consent. So E4 (the approval gate) must land before D5/E2 are connected to the sync job, or the very behavior you want to avoid gets built in.

### Group F: Transparency and UI

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| F1. Models per version subpages | PARTIAL | modularity Stage 4, your requirement | M | The Models page already shows confirmed acc plus CI, bootstrap agreement, macro AUC, per class P/R/F1, confusion, evidence bar, importances, promote, train now, on an expandable card. Missing: dedicated per version pages, a compare two versions view, the feature spec version and its diff, and the provisional/validated badge (A2). |
| F2. AUC curves and drift over time on Models | MISSING | README claim, audit | M | The README advertises AUC curves and drift; the code shows a macro AUC number and a PSI function but no curve or drift chart UI. Either build them or align the README. |
| F3. SHAP per prediction surfaced on Models | PARTIAL | your requirement | S | SHAP is computed at inference for the dashboard "because" strip and global importances are shown; a per prediction SHAP view on the Models or Dashboard page is not. |
| F4. Data analysis panel (selections, tiers, reliability, spec view plus diff) | MISSING | modularity Stage 2 | M | On the Sensors page: per entity keep/role/info tier/reliability/reason, and the feature spec with rationales and a diff against the previous version. |
| F5. Wizard step 7 deepening (consent, cost estimate, spec review) | PARTIAL | modularity wizard | M | The AI Assist step exists; add the stats consent forced choice, the cost estimate, and the reviewable selection plus feature spec output. |
| F6. Wizard step 9 provisional badge | MISSING | modularity wizard | S | Show the first model's metrics with the provisional/validated state. |
| F7. New sensor approval prompt (notification plus inbox style) | MISSING | modularity wizard, your requirement | M | The surface for E4/E5: "3 new sensors found, add them?" with the cost estimate. |
| F8. Settings: levers grouped by stage, stats consent, feature power, discovery toggle | PARTIAL | model_levers, modularity | M | Settings exists for connections and budgets; add the basic and advanced levers from model_levers grouped by stage, the stats consent, the feature power mode, the discovery toggle. |

### Group G: Model family swappability and post processing

| Item | Status | Design ref | Effort | Notes |
|---|---|---|---|---|
| G1. Enrich the `Estimator` port | MISSING | modularity Stage 4 | S | Add `importances()`, `calibrate()`, `supports_sample_weight` to the Protocol so `_fit_node` stops reaching into `est.model.feature_importances_` and `hasattr(est, "calibrate")`. |
| G2. Gradient boosted tree estimator | MISSING | model_levers G1 | M | A second `Estimator` implementation (XGBoost or LightGBM) with early stopping on the temporal split. Offered once labels accumulate. |
| G3. Logistic baseline auto run | MISSING | model_levers G1 | S | Run a logistic model as a silent reported baseline so the Models page can show RF versus linear. |
| G4. Model family selector UI | MISSING | model_levers G1, modularity Stage 4 | S | Depends on G1. Advanced selector on the Models page. |
| G5. Abstain/unknown as a first class output state | MISSING | model_levers G6, modularity Stage 5 | M | Today low confidence is handled implicitly via the evidence cap plus asking. Make unknown an explicit state exposed to HA for automations and honesty. |

---

## Part 3: Recommended build order

Sequenced by dependency and value. Each phase is shippable on its own.

Phase 1, cold start honesty and config (Groups A, B): A1, A2, B2, B3. Small, high value, no LLM work, fixes the biggest risk, and creates the config surface everything else binds to. Optionally A3 if you want the non circular interim signal.

Phase 2, spec driven features as a deterministic capability (Group C): C1, C2, C3, C4, C5, then C6 and C7. Build the feature spec, whitelist, builder, and validator with no LLM. The 13 recipes become the default whitelist so nothing regresses. At the end of this phase a human or the heuristic can author a validated feature spec and the builder executes it; window length is a lever. This is the backbone and the largest phase.

Phase 3, LLM feature architect (Group D): D1, D2, D3, D4, D7, D8, then D5 and D6. The LLM now authors the spec Phase 2 can execute, sees the extended catalog and (with consent) the stats, assigns information tiers, and flags reliability. Supersedes the unimplemented `propose_composites`.

Phase 4, feedback and maintenance (Group E) plus family swap (Group G): E4 first (the approval gate, before any LLM is wired to the sync), then E1, E2, E3, E5. In parallel G1, then G2, G3, G4, G5. The model improves itself within bounds, new sensors flow through detect then ask then approve then background retrain then gate, and the estimator becomes swappable.

Phase 5, transparency and UI (Group F): F1 through F8. The Models subpages, the data analysis panel, the wizard deepening, the approval prompt, and the Settings levers. Much of this depends on the data produced by Phases 1 to 4 (the provisional state, the feature spec, the reliability flags, the cost estimates), so it lands last, though F1/F2/F3 (Models polish) can start earlier since the underlying metrics already exist.

A note on parallelism: Phase 1 and the Models polish parts of Phase 5 (F1, F2, F3) can run alongside Phase 2, because they touch different code. The hard dependency chain is C (builder) before D (LLM authors specs) before E2/E3 (LLM revises specs).

---

## Part 4: Decisions still open (block specific items)

These need your call before the named items can be built cleanly.

1. Cold start gate policy (blocks A3, A1): hold "validated" until a minimum confirmed count (simplest), or evaluate the first model against discovered cluster labels as a non circular interim signal (more work, earlier signal). My lean is the minimum confirmed count plus a clear provisional badge, with the discovered label check as a later enhancement (judgment).

2. Feature power default at first run (blocks C7, D5 scope): ship the conservative role plus composite whitelist first and unlock the full parameterized transforms once trust is established, or go straight to full. My lean is conservative first, because it lets Phases 2 and 3 ship with lower risk and the full set is the same mechanism with more whitelist entries (judgment).

3. New sensor cadence and default (blocks E4): daily or hourly discovery scan, and whether discovery defaults on or off. The scan is cheap and LLM free, so hourly is fine; the approval gate is what protects you. Confirm the default.

4. README alignment (blocks F2): the README advertises AUC curves and drift over time that the UI does not yet render. Decide whether Phase 5 builds them or the README is trimmed to match. Not a correctness issue, but a promise to a user.

---

## Part 5: One paragraph summary

The runtime is built and mostly modular; do not rebuild it. The real work is three things, in order: fix the cold start circularity so the first model is never falsely presented as validated (small, do it first), build a spec driven feature layer so features are a validated executable artifact rather than a fixed recipe registry (the backbone, largest piece, build it deterministically before any LLM touches it), then point the extended LLM at that spec as a feature architect and reliability auditor with stats sharing under explicit consent. Around those, lift scattered constants into config so the Step 4 levers become reachable, enrich the Estimator port so model family is a real selector, change the new sensor sync from auto add to detect then ask then approve so a test sensor never silently burns tokens or retrains, and surface all of it on the Models subpages, the data analysis panel, and the deepened wizard. Four small decisions (Part 4) unblock the rest.

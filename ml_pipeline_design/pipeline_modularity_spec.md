# Pipeline Modularity and UI Spec (Step 5)

Status: spec, June 2026. Read only on the repo. Propose, do not implement.
Purpose: translate Steps 1 to 4 into a stage by stage spec that makes each of the six pipeline stages independently editable and swappable. For each stage: its input and output contract, the settings that should be user editable, sensible defaults, and how a change propagates downstream. Then the interface definitions that let a stage be swapped (a different LLM provider, a different model family) without touching the others, and the X step wizard plus post setup UI that exposes all of it.

The governing principle is the one Hearth already chose (hexagonal core, ports in `domain/ports.py`): a stage is a function with a typed input contract, a typed output contract, and a config object; stages communicate only through their contracts; behavior shaping values are data (settings or a versioned spec), not code constants. Where the current code already meets this, the spec says keep; where it does not, the spec says lift to config.

A single cross cutting object ties it together: the pipeline config. Today behavior is split between compile time constants (`config.py`, module constants in `trainer.py`, `pipeline.py`, `smoothing.py`) and SQLite settings. The spec consolidates all behavior shaping values into one versioned, per instance (and where noted per person) config blob stored in SQLite, with the existing `feature_set_version()` hashing the parts that must force a retrain. This is what makes the levers from Step 4 reachable and what makes change propagation predictable.

---

## Stage 1: Raw input

Input contract: HA connection (URL, token) plus the set of enabled Bindings (entity to role, room, person, options). Optionally a source bucket for history import.

Output contract: rows in `hearth_raw` (one measurement per binding, `num` xor `str`, tags entity_id, role, room, person). This is already a clean, typed boundary.

User editable settings:
- Which entities are bound, and per binding: role, room, person, enabled, options (thresholds, evidence tier override, pet_immune). Basic.
- Ingest cadence and resample grid (new as settings; currently `window_builder_interval` in `config.py` and a hardcoded 1 minute grid). Advanced.
- History import source and cap (`import.max_days`). Basic during wizard, advanced after.

Defaults: 1 minute resample, 5 minute window builder interval, no import cap (take everything), bindings proposed by the LLM or heuristics and confirmed by the user.

Change propagation: adding or removing a binding changes the available raw columns, which changes which features can be built, which (if the feature spec references the new entity) bumps `feature_set_version` and triggers a feature backfill plus retrain. Changing the resample grid changes every feature value, so it must bump the feature set version and force a clean retrain (the grid is part of the feature definition). Changing ingest cadence is safe and propagates nowhere downstream.

Swap interface: `EventSource` and `TimeSeriesStore` ports already exist. A different event source (a non HA hub) or a different time series store (Timescale instead of Influx, ADR-3) is a new adapter implementing the port, wired in `main.py`, with no domain change. Keep as is.

---

## Stage 2: Data analysis (the LLM layer)

Input contract: the entity catalog (Step 3 section a), the target activity list, and the active transform whitelist. Plus, for a maintenance pass, the model feedback summary (Step 3 section f) and the current feature spec.

Output contract: a validated feature specification (`hearth.feature_spec.v1`, Step 3 section d): per entity selections (keep, role, info tier, reliability, reason) plus executable feature definitions. This is the new typed boundary that makes the LLM swappable and auditable.

User editable settings:
- LLM provider and model (already a setting; keep). Basic in wizard, advanced after.
- Aggregate stats consent: the yes/no toggle with implications (Step 3 section e). Basic, forced choice in wizard, editable in Settings. This is a privacy lever, surfaced prominently.
- Feature power mode: conservative (role plus composite selection only, the fallback whitelist) or full (the parameterized transform whitelist). Advanced; default conservative for the first run, full available once the user opts in. Same mechanism, different whitelist contents.
- Budget caps: max entities per call, max features per entity, max total features, max feedback rounds (Step 3 section e and f). Advanced.
- Maintenance trigger settings: new entity discovery cadence (daily or hourly), and the approval requirement (always on; the discovery scan never auto runs the LLM or retrains). Basic on/off for discovery, the approval gate is locked on.

Defaults: model as currently configured, stats consent unset (forced choice), conservative feature mode for first run, discovery daily, approval required, budget caps per Step 3.

Change propagation: a new or revised feature spec is the propagation. When the spec changes (onboarding output, an approved new sensor integration, or an accepted feedback revision), the feature set version bumps, features backfill for the changed columns only, and a background retrain runs, gated by the promotion gate. Changing the LLM model or stats consent affects only future LLM calls, not any stored artifact, so it propagates nowhere until the next analysis is run (and the UI should say so: "changes apply next time you run analysis").

Swap interface: the `LlmAdvisor` port (extended with `propose_feature_spec` and `revise_feature_spec`, Step 3) is the seam. A different LLM provider is a different adapter implementing the port (the current adapter already targets any OpenAI compatible endpoint). The no key path is the heuristic floor implementing the same selection contract (it emits a spec using only the conservative whitelist). Because the output is a validated spec, not free text, the downstream stages cannot tell whether a human, the heuristic, or any LLM produced it. That is the property that makes this stage truly swappable.

UI for transparency (your requirement): a Data analysis panel (in the wizard and on the Sensors page after) shows, per entity, the keep decision, role, info tier, reliability flag, and the one line reason; and a Feature spec view shows every feature with its transform, inputs, window, rationale, and which activities it is expected to separate, plus a diff against the previous spec version. The cost estimate is shown before any LLM run is confirmed.

---

## Stage 3: Feature engineering

Input contract: the feature spec (from Stage 2), the enabled bindings, and raw data from `hearth_raw`.

Output contract: rows in `hearth_features` tagged with `feature_set` version (Step 1 confirmed this is already a clean, persisted, versioned boundary, ADR-7). Keep.

User editable settings:
- Window length (15 / 30 / 60), inference stride, time granularity (coarse / full / none), lag features. Window length and time granularity basic (small choices with plain consequences); stride advanced; training stride locked (anti leakage, Step 4).
- The feature spec itself is editable indirectly: a user can disable a feature, accept or reject an LLM proposed one, or hand add a composite, all through the spec, not code. Advanced.

Defaults: 30 minute window, coarse time, inference stride 5, training stride 30, per role lookbacks as in the registry (now defaults in the spec, not constants).

Change propagation: any change here is a feature definition change, so it bumps `feature_set_version`, forces a clean retrain, and refuses to mix versions in one model (existing behavior, keep). This is the strongest propagation rule in the pipeline and it is already correct; the spec only widens what can trigger it (now including window length and the feature spec, not just composites and time granularity).

Swap interface: today recipes are a closed registry keyed on Role. The change (Step 3, "how this maps onto the code") is to make the builder execute a feature spec, where the 13 existing recipes become the default whitelist entries. After that, a new transform is a new whitelist entry plus a pure builder function, not a change to any other stage. The builder stays a pure function (no I/O), so it is testable and identical across training and inference (no train serve skew).

---

## Stage 4: Model training

Input contract: a feature matrix for one person at one feature set version, plus the labels (bootstrap from rules, overlaid by LLM, discovered, confirmed), plus the training config (Step 4 levers).

Output contract: a `ModelRecord` (version, node, algo, feature_set, path, label_counts, metrics, promoted) plus a serialized estimator artifact. Already typed and clean.

User editable settings (all from Step 4, almost all advanced):
- Model family selector (RF default; GBT once labels accumulate; logistic auto run as a silent baseline). Advanced.
- The training config object: hyperparameters (or auto tune on/off), tuning cadence and data floor, recency half life, train window weeks, class weighting on/off, resampling (off, warned), validation mode (temporal holdout / leave one day out), val days, promotion gate margin, and the new `min_confirmed_before_validated` and `abstain_class` settings. Advanced, except class weighting (basic on/off, "treat rare activities as important").
- Retrain schedule (weekly default) and "Train now" (existing). Basic.

Defaults: the recommended first deployment config in Step 4.

Change propagation: a model family or hyperparameter change triggers a retrain but does not bump the feature set version (features are unchanged), so no backfill is needed, only a refit. The new model competes through the promotion gate; it goes live only if it does not regress confirmed accuracy. A validation mode change affects only reported metrics and the gate, not the model weights. This isolation is why training is swappable without touching features or output.

Swap interface: the `Estimator` port exists but leaks RF specifics (Step 1: `feature_importances_`, the fit signature, `hasattr(est, "calibrate")`). The spec's recommendation is to enrich the port so a family swap is clean:

```python
class Estimator(Protocol):
    def fit(self, X, y, sample_weight=None) -> None: ...
    def predict_proba(self, X) -> pd.DataFrame: ...
    def explain(self, X) -> pd.DataFrame: ...        # SHAP or equivalent; empty if unsupported
    def importances(self) -> dict[str, float]: ...   # NEW: replaces direct est.model.feature_importances_
    def calibrate(self, X_val, y_val) -> None: ...   # NEW: promote from hasattr() to the contract
    @property
    def classes_(self) -> list[str]: ...
    @property
    def supports_sample_weight(self) -> bool: ...    # NEW: so the trainer can branch cleanly
```

With `importances()` and `calibrate()` in the contract, a GBT or logistic estimator drops in by implementing the port, and `_fit_node` no longer reaches into `est.model`. This is the single change that makes Group 1 of Step 4 a real user facing selector rather than a code edit.

UI (your transparency requirement, Models page subpages): one subpage per model version, each showing accuracy_confirmed with its Wilson interval, accuracy_bootstrap shown separately and clearly labeled (never as the headline), per class precision/recall/F1/support, the confusion matrix, AUC (macro and per class), SHAP global importances, the evidence profile stacked bar, the label counts by provenance, the hyperparameters used, the feature set version and its diff from the prior model, and the promotion decision with its reason. A compare view diffs two versions side by side (the registry already stores everything needed; this is assembly, not new computation). The provisional versus validated state (from `min_confirmed_before_validated`) is shown as a badge so a cold start model is never presented as validated.

---

## Stage 5: Output

Input contract: the latest feature windows plus the promoted model(s) for a person (root plus any child nodes), plus the post processing config.

Output contract: `Prediction` objects (predicted, smoothed, confidence, probabilities, explanation, evidence, parent, coarse_confidence) written to `hearth_ml` and published to HA via the `EntityPublisher` port. Already typed and clean.

User editable settings (from Step 4 Group 6):
- Confidence threshold ("how sure before committing", basic slider), abstain/unknown on/off (basic), calibration on/off and method (advanced), smoothing strength (low/medium/high, advanced), per stage post processing toggles (advanced).
- Output channel (HA integration default, MQTT, REST) and the realtime lane on/off. Basic in wizard (channel), advanced after.

Defaults: Step 4 recommended config; HA integration channel; realtime lane on.

Change propagation: post processing changes affect only the served prediction stream, not the model or features, so they are instant and reversible with no retrain. Switching output channel re publishes discovery configs but changes nothing upstream. This is the most loosely coupled stage and should feel instant in the UI.

Swap interface: `EntityPublisher` already abstracts the channel. The spec's one addition (Step 1 finding) is to express the post processing chain (transition filter, hierarchy descent, evidence cap, hysteresis) as an explicit ordered list of toggleable post processors rather than a hand sequenced block in `predict_person`, so a user or developer can disable or reorder a stage without editing the predictor. The order stays internal; the per stage toggles become advanced settings.

---

## Stage 6: Feedback loop

Input contract: predictions plus the asking policy config; user answers (notification actions, inbox, bulk ranges); and, for discovery, the recent feature windows.

Output contract: `LabelEvent` rows (provenance confirmed / discovered / llm / bootstrap) and `ClusterCard` proposals; these feed Stage 4's next training run.

User editable settings:
- Asking policy: ask budget per day per person, quiet hours, confidence and margin thresholds, exploration rate, per person opt out. Budget and quiet hours basic; thresholds and exploration advanced. (Step 1 finding: these are scattered constants today; the spec collects them into one asking policy object so they become reachable levers.)
- Discovery cadence and on/off. Advanced.
- Retrain window and schedule (shared with Stage 4). Basic schedule, advanced window.

Defaults: ask budget 8/day, quiet hours 22 to 8, margin 0.25, ~7 percent exploration, discovery weekly, retrain weekly on a rolling 6 to 8 week window.

Change propagation: feedback produces labels, which change the next training run's inputs, which produces a new model gated by promotion. Naming a cluster labels weeks of history at once and drafts a rule, which also feeds training. Nothing here changes features or the feature set version; it changes the label set only. The loop is therefore decoupled from the feature and model definition and couples only through the label table and the scheduled retrain.

Swap interface: the asking policy object and the `Notifier` port already isolate this. Collecting the thresholds into the policy object (the one change) makes the whole loop configurable from one place and is the prerequisite for exposing these as UI levers.

---

## Stage swap matrix (what changes when you swap each stage)

| Swap | New code | Touches other stages? | Forces retrain? | Forces feature backfill? |
|---|---|---|---|---|
| LLM provider (Stage 2) | New `LlmAdvisor` adapter | No | No (until next analysis run) | No |
| Feature transform set (Stage 3) | New whitelist entry + pure builder fn | No | Yes (version bump) | Yes (new columns) |
| Model family (Stage 4) | New `Estimator` adapter (needs enriched port) | No | Yes (refit) | No |
| Output channel (Stage 5) | New `EntityPublisher` adapter | No | No | No |
| Time series store (Stage 1) | New `TimeSeriesStore` adapter | No | No | No (re-reads same data) |
| Notifier (Stage 6) | New `Notifier` adapter | No | No | No |

The single column that should ever read "Yes, forces retrain" for a definition change is Stage 3 (and Stage 1's resample grid, which is really a feature definition). That is correct and is the existing `feature_set_version` guarantee. Everything else swaps in isolation. This matrix is the test of whether the modularity goal is met: a clean stage swap is a one adapter change with no cross stage edits.

---

## The setup wizard (your X step walkthrough)

The current wizard is 10 steps (Account, HA, InfluxDB, MQTT, Household, Inventory, AI Assist, Activities, Output, Done). The modular pipeline maps onto it cleanly; the spec keeps the count and deepens three steps to carry the new design. Proposed flow, with the stage each step configures:

1. Account. (no stage; auth)
2. Connect Home Assistant. (Stage 1 input)
3. Sensor history and InfluxDB. (Stage 1; existing-or-bundled fork; import cap)
4. MQTT (optional). (Stage 5 alternate channel)
5. Household. (cross cutting; persons drive per person models)
6. Sensor inventory. (Stage 1; automatic export; shows the entity funnel result)
7. AI analysis (deepened). This is the new Stage 2 surface. It contains: the aggregate stats consent yes/no with implications (forced choice, your requirement); the model selector; a cost estimate; then it runs the analysis and shows the reviewable result, namely per entity keep/role/info tier/reliability/reason and the proposed feature spec with rationales. The user approves or edits. With no key, the heuristic floor fills the same screen. This is where "the LLM sets up the model" becomes visible and auditable.
8. Activities. (Stage 6 taxonomy; what the model predicts)
9. Train and output. (Stages 4 and 5) On fast track (history present) this triggers the import, feature build, first train, and predictions, with the live journey narration; it shows the first model's metrics and, critically, the provisional versus validated badge (the cold start honesty fix from Steps 1 and 4). It also mints the integration token.
10. Done. (handoff to the live UI)

The deepening is concentrated in step 7 (AI analysis) and step 9 (train and output); steps 1 to 6 and 8 are essentially the current wizard.

After setup, the running UI is exactly your description (insight, settings, feedback), mapped to the stages:
- Dashboard and Flow map: the live pipeline, predictions, current states. (Stages 5 and 6)
- Sensors page: bindings, the Stage 2 data analysis panel (selections, tiers, reliability), feature spec view and diff. (Stages 1, 2, 3)
- Activities page: taxonomy and rules. (Stage 6)
- Patterns page: discovery cluster cards to name. (Stage 6)
- Models page with per version subpages and a compare view: every metric, SHAP, AUC, confusion, evidence profile, feature spec diff, promotion decision, provisional/validated badge. (Stage 4 transparency)
- Inbox: questions to answer. (Stage 6)
- Settings: connections, LLM provider and model, stats consent, all the basic and advanced levers from Step 4 grouped by stage, the new sensor discovery toggle and approval flow, schedules, tokens. (cross cutting config)
- Methodology page: the existing localized A to Z narration, now also able to explain the feature spec and reliability flags. (read only insight)

The new sensor maintenance flow you described lives across Settings (the discovery toggle) and a notification plus an Inbox style prompt ("3 new sensors found, add them?"); approval routes into a scoped run of step 7's analysis over just the new entities, a background retrain, and the promotion gate decision, with the cost estimate shown first and nothing run until the user says yes.

---

## Summary: what makes each stage independently editable

The whole spec reduces to four moves, three of which are small because Hearth's architecture is already mostly there:

1. Consolidate behavior into one versioned pipeline config (per instance, per person where noted), so the Step 4 levers are data, not constants, and so change propagation is one rule set.
2. Make Stage 3 spec driven (the feature spec from Stage 2 drives a builder over a whitelist; the 13 recipes become default whitelist entries), which is the one substantive new mechanism and the thing that turns the LLM from a role mapper into a feature architect.
3. Enrich the `Estimator` port with `importances()` and `calibrate()` so model family becomes a real selector.
4. Collect the scattered output and asking thresholds into explicit post processing and asking policy objects so they become reachable levers.

Everything else (the ports, the feature store, the versioning, the promotion gate, the provenance tiers) is already modular and is kept. The result is the pipeline you described: a wizard that connects, analyzes, sets up, and trains once, after which the UI is insight, settings, and feedback, with every stage swappable behind its contract and the LLM confined to design and maintenance time, never inference.

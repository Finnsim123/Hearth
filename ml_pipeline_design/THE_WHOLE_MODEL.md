# Hearth — the whole model, under the hood

A single reference for everything the model does: every feature, every clustering
step, how it trains, how it predicts, and how it stays honest. Read §0 for the
mental model, then dip into any section. Every magic number is collected in §11.

Convention: `file.py::function` points at the real code; thresholds are named as
they appear in the source.

---

## 0. The mental model (one page)

Hearth answers one question per person, per 30 minutes: **"what are you doing right
now?"** It is a per-home, per-person classifier — small data (a few thousand
windows), so it favours interpretable trees and a lot of honesty machinery over raw
model power.

The flow, left to right:

```
HA sensors ──▶ ingest ──▶ RAW (InfluxDB, 1-min)
                              │
                              ▼
                        FEATURE WINDOWS  (30-min, one row of ~dozens of columns)
                              │
              ┌───────────────┼────────────────────────┐
              ▼               ▼                         ▼
          LABELS          DISCOVERY                  TRAINING
     (rules + human)   (cluster unexplained      (per-person LCPN:
                        history → name it)         coarse model + child
                              │                    models per parent)
                              ▼                         │
                         markers / new                  ▼
                         activities              PROMOTED MODELS
                                                        │
                                                        ▼
                                                   INFERENCE
                                     (facts bypass ▸ model ▸ hierarchy ▸
                                      transition filter ▸ markers ▸
                                      hysteresis ▸ abstain)
                                                        │
                                                        ▼
                                            PUBLISH to HA + ASK when unsure
```

Three ideas run through all of it:

1. **Roles, not entity names.** Every sensor is bound to a *role* (bed, presence,
   power, media, …). No entity name ever appears in Python; recipes, tiers and
   rules are all keyed on role, so the same code works in any home.
2. **Facts beat guesses.** If a trustworthy sensor *asserts* a state (bed occupied
   → asleep, tracker away → away), Hearth skips the model entirely for that window.
3. **Be honest about what you don't know.** Weakly-evidenced predictions get their
   confidence capped, abstain to `unknown`, and trigger a question instead of a
   confident assertion.

---

## 1. Ingest → raw (`domain/ingest.py`, `adapters/influx_store.py`)

The ingest service subscribes to the **bound** entities over HA's WebSocket, batches
state changes (5-s flush), and writes them to the `hearth_raw` InfluxDB bucket. On
start/reconnect it gap-fills the last 6 h from HA's recorder. Each raw point is
tagged `entity_id, role, room, person` and stored as a numeric field (`num`) or a
string field (`str`) via `coerce_value` — presence/bed/power/etc become numbers;
person home/away and media titles stay strings. (Person state is string-valued,
which is why the Sensors-page liveness check also reads the raw observation count,
not just the numeric sparkline.)

Three buckets: `hearth_raw` (source signals, retention-capped), `hearth_features`
(computed windows, kept), `hearth_ml` (labels, predictions — kept).

---

## 2. The feature pipeline (`domain/features/pipeline.py`)

One code path builds both training matrices and live inference rows (no train/serve
skew). Per person: `raw → prepare → extract_windows → composites → lags → spec
features → impute`. Windows are **30 min** wide, stamped at the window start;
training strides 30 min, the live builder strides 5 min.

`prepare()` resamples raw to a 1-min grid and forward-fills each column up to its
**role's** ffill limit (a bed state persists for hours; a motion blip doesn't).

`extract_windows()` emits, per window row:

- **Temporal** (`time_features`): default "coarse" = `time_bucket` (0 night / 1
  morning / 2 afternoon / 3 evening) + `is_weekend`. "full" adds raw `hour_of_day` /
  `day_of_week` (risks the model memorising a clock; off by default). These are
  stripped before discovery/importance so clustering never keys on the clock.
- **Event dynamics** (`event_dynamics`, CASAS-style): `evt_count`,
  `evt_active_sensors`, `evt_dominant_share`, `evt_idle_minutes` (minutes since any
  direct event, capped 240) — silence at 23:30 says "asleep" louder than any curve.
- **Per-binding recipes**: one recipe per role produces columns `{binding}_{suffix}`
  (e.g. `bed_occupied`, `sofa_frac`, `kitchen_power_on`, `alarm_minutes_until`).
  Recipes carry the role's window length, ffill limit, and absence value.
- **Home mobility** (set-based, from per-room event counts): `mob_rooms_active`
  (range), `mob_top_room_frac` (concentration), `mob_room_entropy` (0 = one room …
  1 = evenly spread = roaming), `mob_room_switches` (order-aware room hops = pacing).
- **Anchor distance**: `dist_to_bed`, `dist_to_door` = BFS hop-distance from the
  window's busiest room to an anchor room, on a room graph learned from observed
  transitions (`refresh_room_graph`, cached daily). Anchors detected by sensor role
  then room-name, so a bedroom with only motion still anchors `bed` — this
  synthesises a sleep cue with no bed sensor. `DIST_CAP=6` when unknown.

`apply_composites()` adds boolean composite features — a data AST (`{"all"|"any":
[…]}`, `{"not": …}`, leaf `{"feat","op","value"}`) over already-computed columns
(e.g. `lights_off_in_bed`). No entity names, evaluated generically, result 0/1.

`add_lags()` appends `{feature}_lag1` (previous window's value) for configured
lag features.

Spec features: an optional LLM-proposed `FeatureSpec` can add extra columns
alongside recipe columns (never overwriting them).

`impute()`:
- **Missingness flags first**: `{binding}_missing` = 1 when the binding produced no
  value this window, computed *before* filling — so the model tells "sensor observed
  off" from "no reading". (A ffill-carried state is not flagged.)
- **Role-semantic fill**: bed's absence = `-1` (sensor absent), everything else `0`
  (no event); lag columns fall back to their base; final `fillna(0)` + assert no NaN.

**Evidence tiers** (`features/evidence.py`): every column is graded 1 DIRECT (bed,
presence, person, media, door, focus, alarm), 2 BEHAVIOURAL (power, light, steps,
custom, **mob_/dist_**), 3 AMBIENT (env, battery), 0 PRIOR (time, composites). A
per-binding `options["tier"]` can override. These tiers drive the confidence cap
(§6) and the model-level evidence profile.

`feature_set_version` (`features/registry.py`) is a SHA of `PIPELINE_VERSION` (now
`"6"`) + every recipe's source + composites + time-granularity + the active spec.
Change any feature definition and the hash changes; both reads/writes of
`hearth_features` and training key off the same string, so **train and serve can
never mix feature versions** (ADR-7).

---

## 3. Labels — where truth comes from (`domain/labeling/`)

Training labels are assembled from four layers, trust-ordered:

1. **Rules / bootstrap** (`rules.py::bootstrap_labels`) — weak supervision. Enabled,
   person-applicable rules are applied by ascending `priority` (lower wins,
   first-writer-per-window); undecided windows default to `home`. Predicates are the
   same JSON AST as composites (no `eval`). Starter rules (`starter_rules.py`) are
   generated day-one from roles: away (needs a real tracker) < sleeping (bed at
   night, or focus/DND fallback) < cooking (kitchen presence + power) < movie (media
   + living presence) < eating (dining presence).
2. **LLM** labels (if a key is set) outrank rules.
3. **Discovered** labels — naming a cluster emits `DISCOVERED` LabelEvents for every
   example window (one click labels weeks of history).
4. **Confirmed** — human answers from the Inbox / notifications; the top of the
   trust order. A subset flagged **gold** are answers to random ε-explore asks — the
   unbiased sample used for the honest headline accuracy.

`merge.py::merge_labels` resolves per-window by that trust order (latest-wins within
a tier), keyed on the 30-min window start. `activity.aliases` folds merged/renamed
activities. `dedupe.py::canonical_activity` stops duplicate activities ("Alex out of
the house" → `away`) — the deterministic safety net behind the LLM.

**Taxonomy** (`taxonomy.py`) is a two-level hierarchy stored as data
(`Activity.parent_id`): coarse states (sleeping / home / away, mutually exclusive)
with fine activities under a parent (cooking / eating / movie / working / chilling
under `home`). `to_coarse` walks a label to its top ancestor; `fine_label_series`
projects labels onto one parent's sub-problem. `MIN_CHILD_WINDOWS=60`.

---

## 4. Clustering & discovery (`domain/discovery/`)

Three distinct "clustering" things happen — don't conflate them.

**(a) Pattern discovery → new activities** (`clustering.py::discover_person`, ~30 d,
weekly/on-demand). Consumes 30-min feature windows that are **not yet human-labeled**
(discovery only explains the unexplained), drops temporal + constant columns,
z-standardises, then PCA to ~90 % variance (≤30 dims) to fight distance
concentration. **Primary:** `HDBSCAN(min_cluster_size = max(8, n//40))`, density-
based, emits clusters + `-1` noise. **Rescue:** a `GaussianMixture` (K by BIC, ≤6)
re-clusters just the noise to surface rare states (cooking, reading) that density
buries. Each surviving cluster gets a **signature** = its top-6 features by |z| vs
the person's global mean (computed in the original feature space so it's readable:
"sofa ↑ · media playing ↑"), an hour histogram, and example windows → a
`ClusterCard`. New cards are deduped against already-handled ones (≥3 of top-4
signature features shared). Needs `MIN_WINDOWS=120`.

**Naming a card → an activity** (`api` + optional LLM). The deterministic *evidence
card* (`evidence.py::build_evidence`) always works with no key: it humanises the
signature (`lexicon.py::humanize_feature`, role+suffix → phrases, tagged by device),
says *when* (smallest hour set covering 70 %), *where* (rooms by |z|), *cadence*
(weekday fraction), what activity tends to come *before/after* (from stored
predictions), and what it *resembles*. With a key, `suggest_cluster_names` sends
**metadata only** (never raw series) and gets 2–3 short names back, always run
through `dedupe_suggestions`. Committing a name emits DISCOVERED labels for the
card's windows and drafts a **disabled** rule from the signature.

**(b) Co-activation** (`coactivation.py::cluster_sensors`) groups *sensors that fire
together* (not by room) for the coverage map's "By behaviour" lens: per-sensor
5-min change intensity → `1 − corr` distance → average-linkage agglomerative cut at
0.7 → MDS 2-D layout (fixed seed) so nearby bubbles genuinely behave alike.

**(c) Lead/lag** (`leadlag.py::lead_lag_edges`) recovers the home's *directed*
temporal wiring: per-minute change intensity, smoothed, cross-correlated across
lags ±15 min for the 16 most-active sensors; keeps directed edges with peak
`r ≥ 0.2` ("kitchen → hob ~5 min"). Feeds marker suggestions and the "how your home
flows" view.

**Markers from clusters / edges** (`markers.py`). A card can be classified as a
transition **marker** instead of an activity (`looks_like_marker`: time-concentrated
and infrequent). A marker binds to the card's dominant signal, is
`excluded_from_model` (never a class), and instead injects a prior at inference
(§6). Lead/lag edges whose *target* sensor defines a state (bed→asleep, tracker→away)
become marker *suggestions* (`suggest_markers_from_leadlag`, `min_strength=0.35`) —
never auto-created; the user confirms.

---

## 5. Training (`domain/training/`)

`trainer.py::train_person` runs per person (default 8-week look-back).

**Gate 1:** `< 100` feature windows → skip. Then drop other members' personal
sensors (`drop_foreign_personal`) and any `model_excluded` bindings, build labels
(§3).

**LCPN — Local Classifier Per Node.** The **root** model is trained on *coarse*
labels (every label projected to its top ancestor). Then **one child model per
parent** that has enough fine detail (`_fit_node` after gates: `≥60` masked windows,
`≥2` classes, `≥20` genuine fine children). Root + children combine at inference so
"home" + "eating" are simultaneously true.

**Per-node fit (`_fit_node`):** temporal train/val split at `val_days=7` (falls back
to 75/25 positional if too thin); fit with **recency weighting** (`0.5 ** age_days /
21` — last week ≈ 2× a month ago). Estimator family from `cfg.model_family`
(`estimators.py`): **random_forest** (default: 300 trees, `min_samples_leaf=5`,
`class_weight=balanced`), gradient_boosting, logistic, or an embedding/JEPA stub.
**Tuning** is RF-only, `RandomizedSearchCV` (15 iters) over a `TimeSeriesSplit(3)`
scored `f1_macro`, only above `tune_min_windows=500`, cached ~30 days. **Calibration:**
per-class isotonic, fit on 60 % of val, honesty measured out-of-sample on the last
40 %, then refit on all val for deployment.

**Metrics (`evaluate.py::evaluate_model`)** stored on the `ModelRecord`:
`accuracy_gold` (+CI) over random ε-explore windows — the honest headline;
`accuracy_confirmed` (+CI) over all human labels (pools hard cases, reads lower);
`accuracy_bootstrap` (vs rule labels — circular, named apart); `auc_macro`;
`per_class` (P/R/F1/support); `confusion`; `coverage_curve` (precision vs coverage
across abstain thresholds); `slices` (accuracy by daypart × activity); `calibration`
(Brier, ECE 10-bin, reliability points); `feature_importances` + `evidence_profile`
(share of importance mass per evidence tier). The **flat baseline** — a plain
multiclass model on the same split — is computed at the root so the UI can say
whether the hierarchy earns its complexity.

**Promotion gate (`promotion_gate`).** A freshly trained node goes live only if it's
**not credibly worse** than the current live model of that node: walk the metric
preference `accuracy_gold → accuracy_confirmed`, and for the first metric where both
have data compare **Wilson lower bounds** with a `promotion_margin=0.02`; if neither
has human labels, fall back to bootstrap agreement. First model always promotes.
`validation_status` = "validated" only past `30` confirmed labels, else "provisional"
(serves, but not presented as trustworthy).

---

## 6. Inference (`domain/inference/`, `domain/foundational/`, `domain/markers.py`)

Two lanes share the model + hierarchy:

**Grid lane** (`predictor.py::predict_person`, scheduled) — the full pipeline for the
dashboard. **Realtime lane** (`realtime.py`) — event-driven (a bound-sensor change,
debounced 3 s, 60-s safety tick), builds one in-memory window, runs model →
hierarchy → transition filter → hysteresis only (no SHAP / markers / facts — that's
the grid lane's job), and on a *state change* fires `hearth_activity_changed` on HA's
bus so automations trigger without polling lag.

The order of operations in the grid lane:

1. **Facts bypass the model.** A PERSON tracker marks `away` windows; any earned
   `asleep` fact (reliability verdict == "fact") gates its windows. Gated windows are
   removed from the model's to-do and emitted directly with `confidence=1.0`,
   `model_version="fact-v0"`. A manual **override** still beats a fact.
2. **Model + hierarchy.** The root model gives coarse `predict_proba`; if the coarse
   winner has a child model, the child picks the fine label. Cold start (no promoted
   model): rules predict at a fixed `0.55` confidence (below the ask threshold, so
   rules solicit feedback).
3. **Transition filter** (`smoothing.py::transition_filter`) — one forward-filter
   step on the coarse row: `blended = probs × prior`, where the prior is the learned
   daypart transition matrix from the previous state, mixed 15 % with uniform so it
   never fully locks. Transition matrices are learned per daypart
   (`learn_transitions_by_daypart`, Laplace-smoothed, only consecutive 30-min
   windows vote).
4. **Markers** inject a time-localised prior: a marker fired at `ts − lead_min`
   boosts its `to_state` and damps the `from` self-loop at the real transition window
   (`apply_marker_prior`, `BOOST=6`, `DAMP=0.3`), scaled by the marker's learned
   `strength` so a wobbly one is only a gentle hint.
5. **Confidence cap.** `window_evidence` = the DIRECT-tier share of |SHAP| for this
   window; if `< 0.25` and confidence `> 0.70`, cap to `0.70` (below the `0.75` ask
   threshold) — a confident-but-weakly-anchored guess asks instead of asserting.
6. **Hysteresis** (`smooth`) — publish a switch only if the challenger matches the
   current state, or wins by `≥0.25`, or wins `2` windows running; else hold the line
   (anti-flicker).
7. **Abstain** (`output.py::apply_abstain`) — if enabled and confidence `<
   abstain_threshold` (default `0.4`), publish `unknown` (a real HA state; automations
   do nothing). Raw prediction is preserved.

**Reliability gate for facts** (`foundational/reliability.py`) decides fact / feature
/ suspect from plausibility (role behaviour: uptime, not stuck, flip rate; sleep also
needs a night block), corroboration (1 − contradiction rate), and label agreement
(F1 vs confirmed truth). `FACT_THRESHOLD=0.80`, `FEATURE_THRESHOLD=0.45`; a role must
have observed enough days (presence ≥3, sleep ≥5). Re-scored every 14 days; a
demotion records an advisory + timeline event.

**Asking** (`labeling/active.py::maybe_ask`). A window is asked about if uncertain
(confidence `< 0.75` or top-2 margin `< 0.25`) or on a random ε roll (`0.07`), then
gated by: questions-enabled, quiet hours, daily `ask_budget_per_day`, a 30-min
cooldown, and 90-min same-label suppression. Sleep-like/silent predictions never push
— they wait in the Inbox for next-morning confirmation. Pure-explore rolls on *not-
already-uncertain* windows become the unbiased **gold** eval set.

---

## 7. The honesty / self-awareness layer

- **Capability** (`domain/capability.py`) — a coarse per-activity verdict for the
  Models page: reliable / learning / unreliable (confused or weak) / blind, from F1
  and support (`F1_RELIABLE=0.70`, `F1_UNRELIABLE=0.50`, `CONFUSE_MAX=0.40`,
  `MIN_GOLD=30`, `MIN_SUPPORT=10`), each with a remedy ("add a motion sensor there").
- **Coverage advisor** (`domain/coverage/`) — blind-spot detection: HA areas /
  devices with no usable bound sensor become "ghost rooms"; works before any model.
- **Drift** (`population_stability_index`, PSI > 0.2 = investigate) — flags when the
  home has changed since training; feeds the Models "Drift & health" panel and
  optional auto-retrain.
- **Self-recognition** (`onboarding/advisor.py::is_hearth_own`) — Hearth's own MQTT
  prediction entities (`sensor.hearth_<p>_activity`, …, device "Hearth") are excluded
  from binding / triage / relevance, so the model can never train on its own output
  (a feedback loop) and they never show as a "new device?" prompt.

---

## 8. Insight surfaces (`domain/behaviour/`)

Descriptive read-outs on the Behaviour page, deliberately *not* framed as health
signals: the **time budget / rhythm heatmap / sequences**, the **body-activity band**
(wearable steps, worn/charging/away), **household co-occurrence**, the **weekly buddy
digest**, and — from the mobility work — **Home footprint** (`footprint.py`: rooms
per active spell, roaming, pacing, WoW trend) and **Daily rhythm** (`rhythm.py`:
autocorrelation at 24 h / 168 h + dominant FFT period → "a very regular daily rhythm,
about a day"). Periodicity lives here, not as a model feature, because a per-person
periodicity value is constant across that person's windows and can't discriminate
within their own model.

---

## 9. Person lifecycle & multi-home portability

Identity is `Person.id` (a stable slug minted once); the name is a mutable label, so
**rename is lossless**. **Forget** erases everything a departing member owns (their
raw + features/labels/predictions keyed on the `person` tag, and the app-DB cascade)
while keeping shared sensors and retraining the rest. **Relink** reclaims history
orphaned under a prior identity by adopting the old id (re-keying bindings, rule
predicates, the user link). See `ml_pipeline_design/behavioural_features_and_lifecycle.md`.

Everything is keyed on **role**, and per-home structure (bindings, rooms, rules,
graph) is data in settings/SQLite — the Python carries zero home-specific logic, so
one codebase serves any home.

---

## 10. Two-minute glossary

- **Window** — a 30-min slice; the unit of prediction and one feature row.
- **Binding** — a sensor entity mapped to a role, with a feature-prefix name.
- **Recipe** — the per-role function turning a sensor's raw series into feature columns.
- **Composite** — a boolean feature defined as a data AST over other columns.
- **Fact** — a trustworthy sensor that *asserts* a state and bypasses the model.
- **Marker** — a sensor whose firing marks a *transition*; nudges the filter, never a class.
- **Cluster / ClusterCard** — an unsupervised pattern in unlabeled history, awaiting a name.
- **Coarse / fine (LCPN)** — the two-level activity hierarchy (state ▸ activity).
- **Gold** — labels from random ε-explore asks; the unbiased accuracy sample.
- **Provenance** — a label's trust tier: bootstrap < llm < discovered < confirmed.
- **Evidence tier** — direct / behavioural / ambient / prior; caps confidence.
- **feature_set_version** — the hash that stops train/serve mixing feature definitions.

---

## 11. Parameter cheat-sheet

| Area | Constant | Value | Meaning |
|---|---|---|---|
| Window | `WINDOW` | 30 min | prediction/feature window |
| Features | `PIPELINE_VERSION` | 6 | bump forces rebuild+retrain |
| Features | `DIST_CAP` | 6 | anchor distance when unknown |
| Features | idle cap | 240 min | `evt_idle_minutes` ceiling |
| Discovery | `MIN_WINDOWS` | 120 | min windows to cluster |
| Discovery | HDBSCAN `min_cluster_size` | max(8, n/40) | density cluster floor |
| Discovery | GMM rescue `max_k` | 6 | components on the noise |
| Discovery | signature `TOP_K` | 6 | features per card |
| Discovery | `DEDUPE_OVERLAP` | 3 | shared top-4 → duplicate |
| Co-activation | linkage threshold | 0.7 | cut at corr ≈ 0.3 |
| Lead/lag | max lag / min r / cap | 15 min / 0.2 / 24 | edge keep rules |
| Taxonomy | `MIN_CHILD_WINDOWS` | 60 | to train a child model |
| Training | `min_train_windows` | 100 | Gate 1 |
| Training | `val_days` | 7 | temporal holdout |
| Training | recency half-life | 21 days | sample weighting |
| Training | `tune_min_windows` | 500 | tune only above this |
| Training | RF | 300 trees / leaf 5 | default estimator |
| Promotion | `promotion_margin` | 0.02 | Wilson-LB slack |
| Promotion | `min_confirmed_for_validated` | 30 | provisional → validated |
| Reliability | fact / feature | 0.80 / 0.45 | verdict thresholds |
| Reliability | sleep night block / frac | 180 min / 0.6 | earn "fact" |
| Reliability | verdict cadence | 14 days | re-score facts |
| Smoothing | hysteresis k / margin | 2 / 0.25 | anti-flicker |
| Smoothing | `UNIFORM_MIX` | 0.15 | transition escape hatch |
| Markers | `BOOST` / `DAMP` | 6.0 / 0.3 | prior injection |
| Markers | suggest `min_strength` | 0.35 | lead/lag → marker |
| Confidence | `WEAK_DIRECT_SHARE` / cap | 0.25 / 0.70 | evidence confidence cap |
| Confidence | `ASK_THRESHOLD` / margin | 0.75 / 0.25 | ask if below |
| Confidence | `EPSILON` | 0.07 | ε-explore rate |
| Confidence | abstain default | 0.4 | publish `unknown` below |
| Rules / facts | rule conf / fact conf | 0.55 / 1.0 | cold-start / bypass |
| Realtime | debounce / safety | 3 s / 60 s | re-predict cadence |
| Capability | F1 reliable / unreliable | 0.70 / 0.50 | per-activity verdict |
| Drift | PSI investigate | 0.2 | population stability |

---

*See also: `ha_hierarchy_design.md` (integration→device→entity scanning),
`behavioural_features_and_lifecycle.md` (mobility/rhythm + person lifecycle),
`device_aware_design.md`.*

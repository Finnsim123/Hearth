# Hearth — Research Notes

> Part of the [Hearth](../README.md) docs · design language in [DESIGN.md](DESIGN.md)

Where the hard problems are, what prior art says, and which bets we're making.

## 1. Lessons already paid for (har-homelab prototype, Apr–Jun 2026)

These are empirical findings from a working single-home deployment, not theory:

1. **iOS drops the notification tag** in `mobile_app_notification_action` events
   (home-assistant/iOS#1666). Any feedback loop keyed on tags silently captures
   zero labels. → Hearth keys on action identifiers + server-side question rows (ADR-6).
2. **HA has no `influxdb.write` service.** Writing labels from HA automations
   requires a `rest_command` or a webhook back into the app. → Hearth receives
   feedback on its own API; HA only forwards events (one dumb blueprint).
3. **Bootstrap-label circularity.** Validation accuracy against rule-generated
   labels measures rule agreement, not reality (prototype's "90%" was this).
   → Headline metric = accuracy on human-confirmed windows only.
4. **Uncertainty-only sampling is biased.** Never asking on confident windows
   means confidently-wrong predictions are never corrected. → ε-greedy asks.
5. **Train/serve skew is easy to create.** Prototype's `prev_label` features were
   constant-0 in training, real values at inference — dead weight. → Single
   persisted feature store consumed by both paths (ADR-7).
6. **Imputation semantics matter more than the model.** "Sensor absent" (−1
   sentinel) vs "event absent" (0) preserved discriminative power across a
   sensor-installation boundary. → Role metadata carries absence semantics.
7. **State-change-only writers** (person/zone trackers) need long lookbacks or
   inference windows see NaN → imputed "away" → false predictions. → Slow-sensor
   flag per role.
8. **Defining "away" as *both* away** taught the model `partner_away → away`,
   mispredicting whenever one person traveled. → Strictly per-person label
   semantics; partner state is context features only.
9. **Semantically engineered composites** (lights_off+in_bed, kaffsch-sign
   pre-alarm, stove fumes+kitchen presence) beat raw aggregations for boundary
   windows. → Recipes are the IP; keep them per-role and shareable.

## 2. The hard problems

### P1 — Label scarcity (the central problem)
A home produces ~48 windows/person/day; a tolerable asking budget is ~5–10
questions/day. Supervised-only learning is permanently label-starved.
Mitigations, layered: weak supervision (user rules as labeling functions),
cluster-then-name (one user action labels dozens–hundreds of windows at once),
bulk time-range labeling in the UI ("yesterday 19–21h = movie"), and —
the research bet — self-supervised pretraining on the unlabeled stream (§4).

### P2 — Per-home heterogeneity
No two homes share sensors, layouts, or routines; a global pretrained classifier
is off the table (and privacy-hostile). What *does* transfer: role-based feature
recipes, rule templates, the asking policy, and possibly self-supervised encoder
weights (§4). Everything user-specific stays local.

### P3 — Multi-person attribution
"Kitchen presence high" — but *who* is cooking? The prototype sidesteps it with
per-person models + person-specific sensors (bed side, phone focus, BLE area).
This remains the weakest link when both residents are home doing different
things — and PETS make it worse: a cat triggering the couch presence sensor is
indistinguishable from a person without per-binding context (future: a
`pet_immune` binding option for mmWave sensors with pet filtering, and pet
flags as a labeling-rule guard). Best known signals: ESPresense/BLE room-level location, per-person
device telemetry. Honest position: windows where attribution is ambiguous get
lower-trust labels; don't pretend to solve it in v1.

### P4 — Segmentation vs fixed windows
Activities don't align to 30-min grid cells; transitions (falling asleep,
finishing dinner) are exactly where models err. v1: fixed windows + sliding
stride + temporal smoothing (hysteresis / HMM-style transition prior) as a
post-processor. Later: change-point detection (e.g. ruptures/PELT on the feature
stream) to propose segment boundaries — also improves cluster quality and lets
the inbox ask about *segments* ("18:40–20:10") instead of windows.

### P5 — Concept drift
Seasons change light/heating patterns; routines change (exams, travel, new job).
Rolling training windows (6–8 weeks) + drift monitoring (population stability
index on key features; confirmed-accuracy trend) + retrain triggers. Surface
drift in the UI rather than silently adapting.

### P6 — Honest evaluation at tiny n
With dozens-not-thousands of confirmed labels, report Wilson/bootstrap confidence
intervals on accuracy, stratify by class, and refuse to promote models on
statistically meaningless deltas (promotion gate uses CI overlap, not point
estimates).

### P7 — Clustering ≠ activities
HDBSCAN finds *sensor regimes*, which may split one activity (movie-on-sofa vs
movie-in-bed) or merge two (reading vs chilling — same sensors silent). The UI
must support: name cluster, merge clusters into one activity, mark cluster as
"not meaningful", split by time-of-day. Treat clusters as *label proposals*,
never as labels.

### P8 — Platform risk
Flux is deprecated in InfluxDB 3.x → `TimeSeriesStore` port (ADR-3). HA WebSocket
API is stable and versioned; companion-app notification behavior is the flakiest
dependency → the feedback loop must never *depend* on notifications (UI inbox is
primary). Tokens at rest: encrypted SQLite column (Fernet, key in `.env`).

## 3. Prior art

| Project | What it is | What Hearth takes |
|---|---|---|
| **Frigate** | Local NVR + HA via MQTT/add-on | The whole product shape: standalone container, own web UI, MQTT-discovered entities, optional add-on packaging. |
| **HASS.Agent / ESPresense** | External apps feeding HA | MQTT discovery as the entity mechanism; availability/birth topics. |
| **CASAS / van Kasteren datasets** | Academic smart-home HAR corpora | Validation that ambient-sensor HAR works (80–95% on 3–8 classes); useful for offline recipe tests without private data. |
| **Snorkel** | Weak supervision framework | The *pattern* of labeling functions + trust-weighted overlay — implemented minimally, no dependency. |
| **river** | Online ML in Python | Candidate for incremental learning post-v1; avoids full retrains. |
| **thesillyhome** (paused) | Predicts actuator states & executes them via AppDaemon | Adopted: recency-weighted training samples. Validated-by-failure: directly actuating from weak models erodes trust — Hearth ships predictions as sensors, humans own automations. Backlog: recorder-DB-as-source mode (no Influx), event-driven inference on binding change. |
| **ha-ml-predictor** (early/AI-generated) | Room-occupancy time-to-event predictions | Two real ideas: PETS as a presence-sensor confounder (now tracked under P3 — pet motion fakes human presence; future per-binding `pet_immune` option), and next-occupied-time MQTT topics — independent validation of the HEPA horizon bet. Also a cautionary tale: agent-generated process litter ≠ product. |
| **Forgis HEPA** (MIT) | Self-supervised JEPA encoder for multivariate time series; horizon-conditioned event prediction; one 2.16M-param model transfers across domains | **The serious research bet (§4):** pretrain on a home's unlabeled stream, fine-tune small heads on few labels; embeddings also upgrade clustering. |
| **Forgis TEMPO** (CC BY-NC-SA, not yet runnable) | Time series → discrete tokens for LLM reasoning | Watch-list only: could one day power NL explanations/QA over sensor history. License + maturity + GPU needs rule it out as a dependency. |
| **Forgis FactoryBench** (CC BY-NC-SA) | LLM benchmark on machine telemetry | Not architecturally relevant; borrow its *engineering craft*: resumable long jobs, result JSON + compare tooling — mirrored in Hearth's training-run artifacts. |

## 4. The HEPA-shaped bet: self-supervised pretraining

Why it fits Hearth's two hardest problems:

- **P1 (label scarcity):** every home has months of *unlabeled* multivariate
  sensor data. JEPA-style pretraining (predict future window embeddings from
  context) needs zero labels. A frozen encoder + tiny classification head can
  then learn from tens of confirmed labels instead of thousands. HEPA's result —
  one small architecture transferring across spacecraft/ECG/server domains
  without per-dataset tuning — is evidence the approach survives domain shift,
  and ~2M params trains overnight on a CPU-class homelab box.
- **P7 (clustering):** clustering *learned embeddings* instead of handcrafted
  features typically yields far cleaner regimes → better Pattern cards.
- **Bonus — event prediction:** HEPA's horizon-conditioned hazard head answers
  "P(activity X starts within Δt)" — anticipatory automations (preheat the
  espresso machine *because wake-up is predicted*, not just scheduled).

Integration plan (deliberately decoupled): the `Estimator` and `Embedder` ports
in `domain/ports.py` are the seams. Phase 4 of the roadmap adds an
`adapters/hepa_embedder.py` behind a feature flag; RF-on-recipes remains the
default until the embedding path beats it on confirmed-label accuracy in a
side-by-side (the model registry makes that comparison a UI screen, not a
notebook). If it never wins on real homes, it stays off — the bet is cheap.

## 4b. LLM advisor vs HEPA — different halves of cold start

A recurring confusion worth pinning down: neither replaces the other.

| | LLM advisor (ADR-12) | HEPA embedder (§4) |
|---|---|---|
| Input | entity *names*, device classes, units, aggregate stats | raw *signal values*, no labels |
| Knows | what "sofa", "kaffsch", "PM2.5" mean in the world | which windows look alike in this home |
| Produces | proposed bindings, composites, taxonomy, draft rules | embeddings → better clustering, few-label heads |
| When | one-shot at onboarding (+ cluster-naming hints) | continuously, after days of recording |
| Cost | per-call API cost, BYO key, optional | local compute, free, optional |
| Failure mode | plausible-but-wrong semantics → human approval gate | clusters without meaning → human naming gate |

Both end in the same place: a human approving a proposal. That's deliberate —
Hearth never lets either system write ground truth on its own.

## 5. Open questions (tracked, not blocking)

- Sliding-window stride at inference: 5 min adds responsiveness but correlates
  consecutive predictions — does smoothing need to know?
- Should bulk-labeled ranges be down-weighted vs notification-confirmed windows
  (recall bias: people remember salient activities)?
- Multi-home federation of *recipes/rules* (not data): a community recipe
  registry à la HACS — distribution question, post-v1.
- Grafana: keep, or is the in-app model/feature explorer enough? Ship as
  optional profile, decide on usage.


## Evidence tiers (added June 2026)

**Question:** can we tell whether a prediction rests on reliable first-degree
signals, and act on it? (Raised after the partner-alarm incident.)

**Literature:**
- HAR surveys split sensors into wearable / object / ambient; ambient
  (temp, CO2, humidity) is environment-sensitive and only weakly
  activity-specific. CASAS — the canonical smart-home corpus — is built
  almost entirely on *binary event* sensors (motion, door) for this reason.
- Home Assistant's Bayesian sensor expresses signal strength numerically
  (prob_given_true / prob_given_false): strong evidence = high likelihood
  ratio, ambient evidence ≈ 1. Tiers are likelihood-ratio buckets.
- The spurious-correlation literature: models latch onto proxies that
  co-vary with the target in one period; the remedy is grouping features
  by trustworthiness and auditing reliance — not blanket exclusion.
- Hierarchical feature-selection work: per-activity subsets beat one global
  subset → keep ambient features (CO2 delta genuinely helps cooking), but
  MEASURE the reliance.

**Design (features/evidence.py):** tier per role, overridable per binding
(options.tier — appealable like all gates): 1 direct (bed/presence/person/
media/door/own-phone), 2 behavioral (power/light/steps), 3 ambient
(env/battery), 0 prior (time features & composites). Consumers:
- trainer → metrics.evidence_profile (importance mass per tier; Models page
  renders it as a stacked bar)
- inference → per-window direct-SHAP share stored on every prediction; if
  < 0.25 while confidence > 0.70, confidence is capped to 0.70 — below the
  ask threshold, so weakly-evidenced predictions ASK instead of assert.
  The HA sensor exposes `evidence` as an attribute for automations.


## Activity hierarchy — LCPN (added June 2026)

**Question:** sleeping/home/away are easy and mutually exclusive; cooking/
working/eating are hard and only exist INSIDE "home" — "home and eating" are
simultaneously true. How do others structure this?

**Literature:** hierarchical classification with a Local Classifier Per
Parent Node (LCPN) is the standard answer (HHAR-net for HAR specifically;
HiClass for sklearn tooling). A root classifier picks the coarse state;
a per-parent classifier distinguishes only that parent's children; prediction
is top-down so the output is always a consistent PATH (home→eating). Flat
multi-class over all leaves is known to be worse: it dilutes the easy coarse
boundary with hard fine distinctions. Truly CONCURRENT activities (eating
WHILE watching a movie) are a separate multi-label problem (BiLSTM+SCCRF in
the literature) — deliberately out of scope; the hierarchy covers state+
activity simultaneity, which is what households actually automate on.

**Design:** Activity.parent_id IS the hierarchy (two levels, user-editable on
the Activities page via "Within"). Trainer: root model on coarse-projected
labels (every window) + one child model per parent with ≥60 fine-labeled
windows ("just home" = the parent slug itself, the abstain class). Registry
and promotion gates are per node (root and home models never compete).
Inference is top-down: root → child of the predicted state; Prediction
carries parent + coarse_confidence; the HA sensor exposes `state_level`
(stable, for automations) alongside the fine state. Asking targets the
UNCERTAIN level: sure-of-home + unsure-of-cooking asks "cooking or eating?"
with sibling alternatives, never re-asks the state.


## Accuracy pack (added June 2026)

Survey of how published smart-home HAR systems reach 95–98% (CASAS line,
Cook & Krishnan "Activity Recognition on Streaming Sensor Data") vs Hearth:

1. **Event dynamics** — the canonical feature set is built on event COUNTS,
   dominant sensor and TIME-SINCE-LAST-EVENT, not only window aggregates.
   Added: `evt_count`, `evt_active_sensors`, `evt_dominant_share`,
   `evt_idle_minutes` (idleness clock, capped 240 min) over direct event
   roles (presence/door/media). PIPELINE_VERSION=2 bumps the feature-set
   hash — old and new schemas never mix (ADR-7).
2. **Learned transition smoothing** — discriminative temporal smoothing:
   a Laplace-smoothed transition matrix learned from the household's own
   coarse-label history (stored per person at train time), applied as a
   forward filter over the classifier's probability stream at inference.
   15% uniform mix = decisive observations can always override the prior.
   Targets the prototype's known error class (bedtime transitions).
3. **Margin sampling** — ask when the top-2 gap < 0.25, not only when the
   winner is weak; the active-learning literature shows margin queries are
   the most label-efficient early on.
4. **Calibration** — per-class isotonic regression fitted on the held-out
   validation split AFTER honest evaluation (metrics never see calibrated
   probabilities); every downstream threshold (ask, evidence cap, gates)
   reads confidence as a real probability. Only fitted when n_val ≥ 100.


## Realtime inference lane (added June 2026)

**Problem:** the grid lane predicts every 5 min on 30-min windows aligned to a
5-min stride — fine for the ribbon, far too slow for automations ("dim lights
AS the movie starts"). And no NEW grid window even exists between 5-min marks.

**Design:** an event-driven second lane running beside the grid lane.
- Ingest already streams every bound-entity change over the HA WebSocket; each
  change marks the affected person(s) dirty on a shared `RealtimeSignal`.
- `realtime_loop` wakes (3 s debounce to coalesce bursts; 60 s safety tick),
  predicts a window ending NOW (built in-memory, never written to the feature
  store so the training grid stays clean), applies the transition filter +
  hysteresis, and on a SMOOTHED-STATE CHANGE writes the prediction and fires
  `hearth_activity_changed` on HA's event bus (POST /api/events/...).
- Automations trigger on that event (`platform: event`) → instant, no polling.
  The per-person sensor still exists for state display (poll 60→15 s).
- Cheap: no SHAP/evidence on this path (the grid lane owns the dashboard
  explanation), so it can run on every sensor change without loading the CT.

End-to-end latency: ingest flush (≤5 s) + debounce (3 s) + predict (<1 s) +
HA event (instant) ≈ under 10 s from sensor change to automation.

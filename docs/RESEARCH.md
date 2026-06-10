# Hearth — Research Notes

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
things. Best known signals: ESPresense/BLE room-level location, per-person
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

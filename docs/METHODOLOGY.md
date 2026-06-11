# Methodology — how Hearth turns sensors into "what you're doing"

> **Purpose of this doc.** It's both the *content* for an in-app **Methodology**
> page and the *spec* for the data that personalises it. Every `{{ variable }}`
> is an injection point: static narrative explaining the method, with live
> numbers from *this* instance spliced in so the page reads as "here is what
> Hearth did **for your home**," not a generic manual.
>
> Injection is one-directional and read-only: the page calls `GET
> /api/methodology` (see §Injection catalog) which returns a flat JSON of these
> variables. Missing values degrade to a neutral phrase (e.g. "your sensors")
> so the page never shows a raw `{{ }}` or a broken sentence.

Tone: second person, calm, honest. Where a step has a known weakness we say so —
the page is also where a curious user learns *why a prediction was wrong*.

The page is organised as the data's journey, A→Z. Each stage: **what happens**,
**why it's done that way**, and the **injection points** that localise it.

---

## A. Connecting your home

Hearth never reaches into the cloud. It reads from two local sources on your
network: **Home Assistant** (the live event stream + recorded history) and
**InfluxDB** (the time-series database those events are stored in). Everything
downstream happens on your own hardware — {{ deployment_host }}.

> Your instance has been recording since **{{ recording_since }}**
> ({{ history_days }} days) and saw **{{ events_24h }}** sensor events in the
> last 24 hours.

If a source isn't connected the page says so and links to the wizard step.

- **Injects:** `deployment_host`, `recording_since`, `history_days`,
  `events_24h`, `ha_connected` (bool), `influx_connected` (bool),
  `influx_mode` (bundled / external).

---

## B. Taking inventory — the entity funnel

Home Assistant exposes *everything*: phone battery levels, sun-elevation
forecasts, printer nozzle temperatures, router uptimes. Most of it says nothing
about what a **person** is doing. Hearth runs your full entity list through a
funnel and keeps only what can carry an activity signal.

> Of **{{ entity_total }}** entities in your Home Assistant, **{{ bindable_count }}**
> passed the funnel and became bound sensors. The other
> {{ entity_filtered }} were set aside.

The funnel, in order:

1. **Disabled / hidden** entities are dropped (HA already considers them noise).
2. **Diagnostics & infrastructure** are blocked by name pattern — `rssi`,
   `uptime`, `cpu`, a Zigbee coordinator's own `chip_temperature`, weather
   `forecast`, and so on. These describe the *machinery*, never the home.
3. **The physics gate** (`is_bindable`): a role can only come from a domain that
   actually carries that kind of state. A `button` or `script` has no state
   stream to read, so it can never be a sensor — this rule is not overridable.
4. **Role suggestion**: what's left is matched to a *role* (next stage). Anything
   with no recognisable role is left unbound but visible, so you can bind it by
   hand.
5. **LLM selectivity** (if a key is configured): a language model re-reads the
   survivors and prunes ones that are technically bindable but useless, and
   rescues ones the patterns missed.
6. **De-duplication** on entity id.

> This is a **cold-start prior**, not a claim that filtered entities are
> signal-free. A filtered sensor can be re-admitted later if the data shows it
> carries signal (see §Z). On your instance the filter currently sets aside
> categories like: {{ filtered_examples }}.

- **Injects:** `entity_total`, `bindable_count`, `entity_filtered`,
  `filtered_examples` (a few representative excluded entity ids),
  `llm_assist` (bool — whether the LLM pass ran).

---

## C. Roles — the household-independent abstraction

A model trained on *entity names* would only work in the home it was trained in
(`presence_sensor_sofa` means nothing in someone else's house). So Hearth never
learns on entity names. Every sensor is assigned a **role** — `presence`, `bed`,
`power`, `media`, `env`, `person`, … — and all features are computed from the
role, not the name. This is what makes the method portable and what lets you
swap a sensor without retraining from scratch.

> Your home maps to **{{ role_count }}** distinct roles:
> {{ role_breakdown }}.

The role also carries metadata the rest of the pipeline reads: how long to
forward-fill it, what "missing" means for it, how far back to look (§H), and its
evidence tier (§M).

- **Injects:** `role_count`, `role_breakdown` (role → sensor count, e.g.
  "presence ×6, env ×9, power ×4"), `unbound_count` (recognised entities you
  haven't bound).

---

## D. Rooms & coverage

Each sensor is tagged with the **room** (Home Assistant *area*) it lives in.
Hearth doesn't predict per-room, but it uses room coverage to tell you *where it
can see well and where it's blind* — the bubble chart on the dashboard. Room
names are canonicalised (case + spelling folded, semantic duplicates merged) so
a rescan can't split one room into `Living_room` and `livingroom`.

> You have sensors in **{{ room_count }}** rooms: {{ room_list }}. The room with
> the least direct coverage right now is **{{ weakest_room }}** — adding a
> presence sensor there is the single highest-leverage improvement.

- **Injects:** `room_count`, `room_list` (room → sensor count), `weakest_room`,
  `unassigned_count`.

---

## E. Importing your history

If you arrived with existing data, Hearth imports **all of it**, not a fixed
window — it probes the earliest timestamp in your database and backfills forward,
one month-sized chunk at a time so even years of history never overflow memory.
History is what lets the model skip the cold-start waiting week.

> Hearth imported **{{ imported_points }}** data points spanning
> **{{ import_span_days }}** days. {{ pruned_note }}

> **Honest caveat:** the model recency-weights training data with a
> **{{ recency_half_life }}-day half-life**, so data older than a few months
> contributes very little to the *current* model even though it's all kept on
> disk for discovery and future retraining.

- **Injects:** `imported_points`, `import_span_days`, `pruned_note` (e.g.
  "12 sensors had no history and were disabled"), `recency_half_life`.

---

## F. Normalising the signal

Sensors report at wildly different rates — some every second, some only when
they change. Feeding that straight to a model would let a chatty sensor drown out
an important-but-quiet one purely by volume. So every sensor is **resampled to a
common 1-minute grid** (last value wins), then **forward-filled** for a
role-specific number of minutes (a presence reading goes stale in 5 minutes; a
"person home" reading stays valid for a week). After this step, sampling rate no
longer biases anything — only *what the sensor said* matters.

- **Injects:** none required (this stage is identical across homes); optional
  `resample_grid` ("1 minute") for completeness.

---

## G. Windowing — the unit of prediction

Hearth never classifies a single instant. It classifies a **30-minute window**:
to decide what you're doing *now*, it looks at the last half hour of normalised
signal and summarises it. The window **end** is shared by every sensor (so all
features describe the same moment), but the **lookback is role-aware** — a motion
sensor looks back {{ window_presence }} minutes (recent matters), a step counter
looks back {{ window_steps }} minutes (it only means something over hours).

> Windows slide every 5 minutes for the live ribbon and every 30 minutes for
> training. Your current feature set uses these per-role windows:
> {{ role_windows }}.

Window length ≠ latency: the live lane re-evaluates a fresh window the instant a
sensor changes, so reaction is near-instant while each prediction still carries
30 minutes of context.

- **Injects:** `window_minutes` (30), `role_windows` (role → minutes map),
  `window_presence`, `window_steps`, `stride_live` (5), `stride_train` (30).

---

## H. Feature engineering — the recipes

For each window, every sensor's role runs a small **recipe** that turns its raw
sub-series into a few numbers (the *features*). Presence → fraction of the window
active, plus transition count. Bed → mean/max pressure, occupied flag.
Environment → mean, delta, max. These are deliberately *aggregations only* — the
model learns the thresholds itself; Hearth never hard-codes "CO₂ > 1200 = cooking."

> Your windows currently produce **{{ feature_count }}** features from
> {{ sensor_count }} sensors, identified by feature-set version
> **{{ feature_set_version }}**.

- **Injects:** `feature_count`, `sensor_count`, `feature_set_version`,
  `feature_examples` (a handful of column names like `couch_frac`,
  `bed_a_occupied`).

---

## I. Event dynamics & time

On top of the per-sensor recipes, Hearth adds two cross-cutting feature families:

- **Event dynamics** (count of state changes in the window, which sensor
  dominated, and **minutes of silence** before the window end). Forty silent
  minutes at 23:30 says "asleep" louder than any single sensor.
- **Time**, encoded *coarsely* on purpose — a 4-bucket part-of-day
  (night/morning/afternoon/evening) + weekend flag, **not** raw hour-of-day. Raw
  hour lets a tree memorise "at 19:00 they're usually cooking" and stop reading
  the sensors — the *clock-crutch* failure. Coarse time keeps the legitimate
  "it's night-ish" prior without the lookup table.

> Your time encoding is set to **{{ time_granularity }}**.

- **Injects:** `time_granularity` (coarse / full / none), `event_features`
  (the evt_* column names).

---

## J. Composites & starter rules

Some signals only mean something *in combination* — "TV playing **and** on the
sofa **and** lights low" = movie. Hearth expresses these as **composites** and
**rules**, stored as **data** (a JSON expression tree), never as code. That's
what keeps them safe to generate (an LLM can propose one without us running
arbitrary logic) and editable by you on the Activities page.

> You have **{{ composite_count }}** composite features and **{{ rule_count }}**
> labelling rules in play.

- **Injects:** `composite_count`, `rule_count`, `composite_names`.

---

## K. Evidence tiers

Not all "evidence" is equal. A bed pressure sensor *directly* tells you someone's
in bed (tier 1). A power spike is *behavioural* (tier 2). Room temperature is
*ambient* (tier 3). Roles are mapped to tiers, and the tier is used two ways: to
colour the coverage chart, and to **cap confidence** — if a high-confidence guess
rests only on weak ambient evidence, Hearth knocks the confidence down so it
doesn't over-trust a coincidence.

> Across your live sensors: {{ tier_breakdown }}.

- **Injects:** `tier_breakdown` (tier → count), `weak_evidence_cap` (0.70).

---

## L. The activities it predicts

> Your taxonomy currently has **{{ activity_count }}** activities:
> {{ activity_list }}.

Activities can be **hierarchical** — a coarse state (`home`) with fine children
(`cooking`, `movie`, `working`). Hearth predicts top-down: first the coarse
state, then the fine activity if there's enough labelled data for it. A child
model only spins up once it has enough examples, so the system gracefully starts
coarse and sharpens over time.

> Your hierarchy: {{ hierarchy }}.

- **Injects:** `activity_count`, `activity_list`, `hierarchy` (parent → children),
  `silent_activities` (ones Hearth never sends notifications for, e.g. sleeping).

---

## M. Cold-start labels (bootstrapping)

A model needs labelled examples, and on day one you have none. So Hearth
**bootstraps**: the starter rules (§J) label windows automatically — "bed
occupied + night + someone home → sleeping." These labels are noisy but get the
model off the ground. As real confirmations arrive, they take priority.

> Your training set right now is **{{ bootstrap_label_count }}** bootstrap labels
> + **{{ confirmed_label_count }}** confirmed labels from you.

- **Injects:** `bootstrap_label_count`, `confirmed_label_count`,
  `label_class_balance` (class → count).

---

## N. Active learning — when (and how) it asks

Hearth improves by asking, but it asks *sparingly and smartly*. After each
prediction it computes a **confidence** and a **margin** (gap between the top two
guesses). It only asks when it's genuinely unsure — low margin — and only up to a
daily budget, and **never for silent activities** (it won't wake you to ask if
you're asleep). A confirmed answer is written back as a high-priority label.

> Confidence threshold for asking: **{{ ask_threshold }}**. Daily question
> budget: **{{ ask_budget }}**. Questions asked today: **{{ questions_today }}**.

- **Injects:** `ask_threshold` (0.75), `ask_budget`, `questions_today`,
  `margin_sampling` (bool).

---

## O. The model

The classifier is a **Random Forest** — an ensemble of decision trees. It's
chosen deliberately over a neural net: it's robust to uninformative features on
small datasets, needs no GPU, trains in seconds on your hardware, and is
*interpretable* (it can tell you which sensors drove a decision). For
hierarchical taxonomies Hearth trains one forest per node (coarse root + a child
per parent — "Local Classifier per Parent Node").

> Your live model is version **{{ model_version }}**, trained
> **{{ model_trained_at }}** on **{{ train_window_count }}** windows.

- **Injects:** `model_version`, `model_trained_at`, `train_window_count`,
  `n_nodes` (how many sub-models in the hierarchy).

---

## P. Honest evaluation

Accuracy is measured on a **held-out time slice** the model never trained on
(the most recent days), never on data it has seen — otherwise the score is a lie.
Classes that don't appear in training are excluded from the score (you can't grade
a model on something it was never taught). Training itself **recency-weights**
windows with a {{ recency_half_life }}-day half-life, so last week counts far more
than last month.

> On its held-out test, your model scored **{{ model_accuracy }}** overall.
> Per-class: {{ per_class_f1 }}. Its most error-prone case:
> {{ worst_class_note }}.

- **Injects:** `model_accuracy`, `per_class_f1` (class → F1), `worst_class_note`,
  `test_window_count`.

---

## Q. Calibration & smoothing

Two corrections sit between the raw forest and what you see:

- **Calibration** (isotonic, per class): a forest's "70%" isn't always *really*
  70%. Calibration re-maps the numbers so a stated confidence matches the real
  hit-rate — important because the asking policy (§N) trusts those numbers.
- **Transition smoothing**: Hearth **learns your transition matrix** — how often
  each state really follows another (you rarely go sleeping → cooking in one
  step) — and uses it to damp implausible single-window flickers. It's a learned
  prior, mixed lightly so it nudges rather than overrides.

> {{ calibration_note }} {{ transition_note }}

- **Injects:** `calibration_status` (active / not enough data), `calibration_note`,
  `transition_note` (e.g. "learned from {{ confirmed_label_count }} confirmations").

---

## R. Promotion gate

A freshly trained model doesn't go live automatically. It must clear a **gate** —
enough training windows, enough classes, and not materially worse than the model
it would replace. If it fails, the previous model stays in service and the page
tells you why. This is what stops a bad week of data from degrading predictions.

> Your last training run: **{{ last_train_outcome }}**.

- **Injects:** `last_train_outcome` (promoted / rejected + reason),
  `min_train_windows`.

---

## S. Serving predictions — two lanes

Predictions run on two lanes simultaneously:

- **Grid lane** — every 5 minutes, fills the history ribbon and dashboard.
- **Realtime lane** — event-driven: the instant a bound sensor changes, Hearth
  re-evaluates the live window and, **if the smoothed state actually changed**,
  fires a `hearth_activity_changed` event on Home Assistant's bus so your
  automations trigger with no polling lag.

> In the last 24 hours Hearth produced **{{ predictions_24h }}** predictions.
> Right now it thinks: {{ current_states }}.

- **Injects:** `predictions_24h`, `current_states` (person → state + confidence).

---

## T. Notifications & who gets what

Each household member has a **notification role**. The admin gets system messages
(model went live, something needs attention); everyone else only gets the
training questions about *their own* activity. Nobody is asked about someone
else's state, and silent activities never notify.

> Members: {{ member_roles }}.

- **Injects:** `member_roles` (name → notification role), `notify_channel`.

---

## U. Discovery — finding activities you never named

Once a week Hearth clusters the windows it *couldn't* confidently explain. A tight
cluster that recurs is probably a real activity you haven't named yet ("every
weeknight 21:00, sofa + TV + low light"). It surfaces these on the Patterns page
as unnamed candidates — naming one labels weeks of history in a click.

> Last discovery run found **{{ patterns_found }}** candidate patterns;
> **{{ patterns_pending }}** are waiting for you to name or dismiss.

- **Injects:** `patterns_found`, `patterns_pending`, `discovery_last_run`.

---

## V. The self-improvement loop

Everything above is a cycle, not a one-shot:

1. Predict → 2. Ask when unsure → 3. You confirm → 4. New labels stored →
5. **Weekly retrain** on a rolling window of recent labels → 6. Gate → 7. Promote.

> Schedule: discovery **{{ discovery_schedule }}**, retraining
> **{{ retrain_schedule }}**. Each retrain uses a rolling
> **{{ retrain_window_weeks }}**-week window of labels.

Whenever the *method itself* changes (a new feature, a different window), the
**feature-set version** changes, which forces a clean retrain so old and new
feature definitions never mix in one model.

- **Injects:** `discovery_schedule`, `retrain_schedule`, `retrain_window_weeks`,
  `feature_set_version`, `next_retrain_at`.

---

## W. What Hearth never does (privacy)

A short, fixed section — no injection. Raw history never leaves your hardware;
LLM calls (if enabled) send entity **metadata and aggregate stats only**, never
raw sensor streams; no cloud account is required; you can delete any sensor,
label, or model from the UI. State this plainly because it's the question every
new user actually has.

- **Injects:** `llm_enabled` (bool) + `llm_model` (only to name what's used, if
  enabled); otherwise static.

---

## Injection catalog (build spec for `GET /api/methodology`)

One endpoint returns a flat object. Sources are existing repo/store calls — no
new computation, just assembly. Grouped by where the value already lives:

| Variable | Source |
|---|---|
| `deployment_host`, `influx_mode`, `ha_connected`, `influx_connected` | `repo.get_connection(...)`, settings |
| `recording_since`, `history_days`, `events_24h` | `tsdb.first_raw_time`, `journey()` |
| `entity_total`, `bindable_count`, `entity_filtered`, `filtered_examples`, `llm_assist` | last inventory scan (cache in a setting at scan time) |
| `sensor_count`, `role_count`, `role_breakdown`, `unbound_count` | `repo.bindings()` aggregation |
| `room_count`, `room_list`, `weakest_room`, `unassigned_count` | `repo.bindings()` + `binding_tiers` |
| `imported_points`, `import_span_days`, `pruned_note`, `recency_half_life` | `fasttrack.status`, `trainer.RECENCY_HALF_LIFE_DAYS` |
| `window_minutes`, `role_windows`, `window_presence`, `window_steps`, `stride_*` | `registry` recipes |
| `feature_count`, `feature_set_version`, `feature_examples`, `time_granularity`, `event_features` | `feature_set_version()`, a sample feature row's columns |
| `composite_count`, `rule_count`, `composite_names` | `repo.get_setting("composites")`, `repo.rules()` |
| `tier_breakdown`, `weak_evidence_cap` | `binding_tiers`, constant |
| `activity_count`, `activity_list`, `hierarchy`, `silent_activities` | `repo.activities()` |
| `bootstrap_label_count`, `confirmed_label_count`, `label_class_balance` | `tsdb.read_labels`, `/bindings/health` classes |
| `ask_threshold`, `ask_budget`, `questions_today`, `margin_sampling` | settings, `repo.questions_since` |
| `model_version`, `model_trained_at`, `train_window_count`, `n_nodes` | `repo.models()` (promoted root) |
| `model_accuracy`, `per_class_f1`, `worst_class_note`, `test_window_count` | `model.metrics` |
| `calibration_status/note`, `transition_note` | `model.metrics`, `repo.get_setting("transitions.*")` |
| `last_train_outcome`, `min_train_windows` | `repo.models()` history, `trainer` constants |
| `predictions_24h`, `current_states` | `tsdb.read_predictions` |
| `member_roles`, `notify_channel` | `repo.persons()` |
| `patterns_found`, `patterns_pending`, `discovery_last_run` | `repo.clusters()` |
| `discovery_schedule`, `retrain_schedule`, `retrain_window_weeks`, `next_retrain_at` | scheduler config |
| `llm_enabled`, `llm_model` | `repo.get_connection("llm")` |

**Rendering rules for the page**
- Every injected value has a **fallback phrase** so a fresh install (no model,
  no labels) still reads as complete prose — e.g. `model_accuracy` missing →
  "your model hasn't finished its first training yet."
- Numbers that change are rendered with light emphasis so the page doubles as a
  glanceable status sheet.
- Each stage is collapsible; a short one-line summary is always visible, the full
  explanation expands. Deep-link anchors (`/methodology#windowing`) so other
  pages can point here ("why 30 minutes? →").
- A single "as of {{ generated_at }}" timestamp at the top.

**One thing to decide before building:** depth toggle. Either (a) one page with
expandable stages (recommended — skimmable + deep), or (b) two reading levels
("Plain" vs "Technical") switched at the top. I'd build (a) first.

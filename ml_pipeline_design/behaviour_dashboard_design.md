# Hearth — Behaviour section (habits & routines dashboard)

Status: brainstorm / design direction, June 2026. A new top-level menu section that
turns Hearth's per-person activity timeline into insight: how long each person spends
on each activity, when, how consistently, and how it's changing.

## 0. What we already have (so this is mostly visualisation, not new ML)
- A per-person, per-window (30-min) **labelled timeline** — the published predictions,
  each with `confidence` and a `basis` (fact / model / rule / unknown).
- **Activity colours** (`Activity.color`) → a cohesive palette for free.
- A **learned transition matrix** per person (the HMM forward filter) → sequences for free.
- **Foundational facts** → away/asleep durations are *certain*; in-home fine activities
  are softer. The dashboard should lean on that distinction (below).
- Confirmed labels + abstain/unknown windows → an honesty layer.

## 1. The honesty constraint (the thing that makes this trustworthy, not creepy)
This is self-tracking on inferred data in a home — it must be **descriptive, not
judgmental**, and **honest about certainty**:
- Distinguish **known** (fact: away/asleep, confirmed labels) from **inferred** (model).
  Render inferred segments slightly lighter / hatched, and show a **coverage stat**
  ("84% of today classified · 16% unknown") so nothing is presented as harder than it is.
- While a person's model is **provisional**, badge the stats as rough.
- Granularity caveat: durations quantise to the window (30 min) unless we read the raw
  event timeline — state it.
- **No goals, scores or nagging by default.** Frame as "here's what happened," never
  "you should." Anything prescriptive (targets, streak pressure) is opt-in.
- **Per-person comparison is opt-in / consensual** (housemates) — local-first helps, but
  social dynamics don't. Default: each person sees their own; comparison is a deliberate toggle.

## 2. The panels — what gives insight, and how to show it (prioritised)

### A. Today — the day so far  *(MVP)*
- The **day ribbon**: a horizontal 24h timeline, segments coloured by activity (you
  already have this on the dashboard — reuse it here per person).
- Current activity + **how long** they've been in it; today's **totals** ("slept 7h10m,
  cooking 40m so far").
- *Insight:* at-a-glance "how has today gone."

### B. Time budget — where the time goes  *(MVP, highest universal value)*
- **Stacked bars, one per day** (last 7/30): hours per activity stacked → see how each
  day is composed and how composition shifts.
- A **donut/treemap** of total share over the range ("this week: 33% sleep, 9% cooking…").
- Per person, with a household roll-up.
- *Insight:* the "where does my time actually go" reveal — the most compelling single view.

### C. Daily rhythm — when things happen  *(high "aha")*
- A **24h × day-of-week heatmap per activity** (or a radial 24h clock): when do you
  usually cook / sleep / watch a movie? Darker = more often.
- A **"typical day"** averaged ribbon (the modal activity per time-of-day).
- *Insight:* your routines made visible — and where they're fuzzy.

### D. Sleep & away (the trustworthy ones — facts)  *(strong, because it's certain)*
- Because these come from foundational facts, the numbers are reliable: **bedtime /
  wake-time over time**, sleep **duration trend**, time **out of the house** per day.
- A bedtime/wake "consistency band" (how much they vary).
- *Insight:* the most actionable, least-caveated panel — sleep regularity, time away.

### E. Durations & sessions
- Per activity: **average/median session length**, distribution, sessions/day, longest.
- *Insight:* "cooking sessions average 35 min; movies 1h50m."

### F. Consistency / routine strength
- Variability of key event times (wake, first-meal) → a plain-language **routine read**
  ("very regular wake time ±15 min" vs "varies ±90 min").
- **Streaks** (opt-in framing): "5 days running you were asleep before midnight."
- *Insight:* routine vs chaos, without a judgmental score.

### G. Sequences — what follows what  *(cheap + unique: reuse the transition matrix)*
- A small **Sankey / chord** of the learned transitions: dinner → movie → sleep.
- "After cooking you usually eat (82%); after a movie you usually sleep."
- *Insight:* your home's narrative flow — and it's already computed.

### H. Trends & changes — the insight engine
- Week-over-week / month-over-month **sparklines + delta callouts**: "sleeping ~40 min
  less this week," "more cooking this month," "movie time up."
- Surface only *notable* changes (effect size threshold) so it's signal, not noise.
- *Insight:* "what changed" — the thing people actually come back for. (Conceptually the
  same machinery as drift detection, pointed at behaviour instead of features.)

### I. Co-occurrence / household (optional, opt-in)
- When person A is X, person B tends to be Y; weekend vs weekday differences.
- *Insight:* household rhythm — but gated behind consent.

## 3. Visualisation guidance
- **One palette** = the activity colours already in the taxonomy; keep it identical
  across every panel so a colour always means the same activity.
- **Ribbon** for a day, **stacked bars** for composition over days, **24h×7 heatmap**
  (or radial clock) for rhythm, **Sankey/chord** for sequences, **sparkline + delta**
  for trends, **small multiples** per person.
- Inferred vs known: lighter fill / hatch for model-inferred; solid for facts/confirmed.
- Every chart **drillable**: click a segment → the window's "why" (you already compute
  SHAP evidence); click a trend → the days driving it.
- Controls: date-range, per-person filter, activity filter.

## 4. Hearth-specific wins (what makes this *yours*, not a generic life-tracker)
1. **Honesty layer** (known vs inferred + coverage) — differentiates from creepy
   quantified-self apps; fits the glass-box ethos.
2. **Reuse the transition matrix** for the sequence view — near-zero extra work, novel.
3. **Facts make sleep/away analytics trustworthy** — lead with those.
4. **Buddy narration**: a weekly "behaviour digest" ("you went to bed later midweek;
   cooking up on weekends") via the existing insight/health channel — descriptive, opt-in.

## 5. Naming & relation to the existing "Patterns" page
"Patterns" already means *unsupervised discovery of new activities to name*. This is
different — analytics on the activities you already track. Name it **"Behaviour"** (or
"Routines" / "Rhythm"). Cross-link: a discovered-and-named pattern starts showing up
here once it's a real activity.

## 6. Data source & quality notes
- Aggregate the **published (smoothed) prediction timeline** — that's what the home
  acted on, and it already blends facts + model + abstain. (Not the raw model argmax.)
- Treat `unknown`/abstain windows as their own "unclassified" band, counted in coverage.
- Quantisation: 30-min windows → durations are coarse; fine for trends, note it for
  "session length." A later refinement can use raw state-change timestamps for exact edges.

## 7. Build order
1. **MVP**: Today ribbon + Time-budget (stacked bars + donut) per person, over a
   date-range, with the coverage/known-vs-inferred honesty. One new page + one
   aggregation endpoint over the prediction store.
2. Daily-rhythm heatmap + Sleep/away facts panel.
3. Sequences (transition matrix) + Trends/deltas + buddy weekly digest.
4. Sessions/consistency, household co-occurrence (opt-in).

## 8. Open decisions (for you)
1. **Scope of v1**: just *time budget + today* (broad appeal, simplest), or include the
   *rhythm heatmap* in v1 (higher wow, a bit more work)? My lean: budget + today + the
   sleep/away facts panel, because facts make it trustworthy from day one.
2. **Comparison default**: per-person only, with household/comparison strictly opt-in?
   (My lean: yes — privacy/social-safety first.)
3. **Any goals/streaks at all**, or purely descriptive v1? (My lean: descriptive only;
   add opt-in goals later if you want them.)
4. **Granularity**: live with 30-min quantisation for v1, or read raw state-change
   timestamps for exact session edges? (My lean: 30-min for v1, exact later.)

## 8b. Built since v1 (June 2026)
- **Rhythm heatmap** (24h×7, dominant-activity or per-activity filter) and **Sequences**
  ("what follows what" — observed change-transitions from the published timeline, not
  the stored HMM matrix: more honest).
- **Trends / "what changed"** (`summary.trends`): last 7d vs prior 7d per activity,
  notable-only (absolute + relative floor, or clean start/stop), fact/inferred basis.
  Route fetches 14d for trends regardless of the view toggle. Shared notability lives
  in `make_callout`.
- **Active vs sedentary** (`body.py`): worn windows split by step-rate
  (`ACTIVE_STEP_WINDOW`); per-day + totals + a WoW trend (`active_sedentary_trends`,
  reuses `make_callout`). Trustworthy now that charging excludes docked time.
- **Weekly buddy digest** (`behaviour/digest.py`): descriptive recap (time budget →
  sleep/away facts → what changed → steps/active → coverage footer) sent via the
  existing notifier. Scheduled Mon 08:00. Double opt-in: global
  `behaviour.digest.enabled` (toggle on the Behaviour page) AND per-person
  `notify_system`. `compose_digest` is pure/tested.
- **Sessions & consistency** (`summary._episodes/_sessions/_consistency`): per-activity
  episode count + mean/median/longest (note: 30-min quantisation makes *length* coarse,
  *count* fine); wake/bed regularity bands from night-sleep facts (bedtimes measured
  from 18:00 so they don't wrap midnight).
- **Household co-occurrence** (`behaviour/household.py`): the one cross-person view —
  "when A is X, B is usually Y (p%)" (window-aligned cross-tab). STRICTLY opt-in and
  consensual: consent stored per person in settings (`behaviour.share.<id>`); the
  endpoint returns nothing unless the viewer has shared AND ≥2 people have. UI has a
  per-person share toggle. *Known limitation (research-preview):* the toggle acts on the
  selected person, not the authenticated user — in a true multi-user deployment this
  should be gated to the logged-in user's own person via `request.state.user`.

- **Drill-down "why"** (basis-aware): clicking a per-window surface opens a reusable
  `WhyModal`. For MODEL windows it calls the existing `POST /api/predict/probe`
  (whatif.probe_window) and shows calibrated probabilities + the SHAP top-signals
  (diverging bars). For FACT/RULE/UNKNOWN windows it does NOT fabricate a model
  explanation — it says plainly "known: a sensor reported this, model bypassed" /
  "cold-start rule" / "not classified". Wired on the Today ribbon (per segment) and
  Sessions rows (latest window of that activity, carried as `Session.last_ts/last_basis`
  via `last_seen`). Aggregate panels (rhythm/day-bars/trends) are intentionally NOT
  click-to-SHAP — a single explanation can't honestly represent many windows.

## 9. Body / activity sensors (steps, distance, floors, motion) — added June 2026

HA exposes wearable counters (steps, distance, floors climbed) and instantaneous
activity signals (motion/occupancy, `walking/still` wearable states). These split
into two signal types that want opposite treatment, and two independent questions.

**Signal types.**
- *Cumulative counters* (steps/distance/floors) — monotonic daily totals that reset
  at midnight. Already modelled in the schema: `Role.STEPS` + `InfoTier.CUMULATIVE_COUNTER`
  (T4, "only its rate matters"). Raw value is useless; the **per-window increase** is
  the signal. Resets and not-worn gaps must be handled.
- *Instantaneous* (motion/occupancy, wearable activity state) — already per-moment;
  per-room motion is the most discriminative HAR feature and almost certainly belongs
  in the feature set already.

**Q1 — feed the MODEL?** Decision (Finn, June 2026): **let the model decide.** Bind the
counters as `Role.STEPS`; feature selection / regularisation drops them if they don't
earn their place, so no manual leakage gymnastics. One recorded caveat: selection
protects the *classifier* but NOT unsupervised *discovery* — a body signal that
strongly proxies "away" can crowd out in-home discovery. The existing
`Binding.model_excluded` flag is the lever if that ever shows up (built into features +
seen by discovery, dropped before training). No code needed now.

**Q2 — enrich the DASHBOARD?** Yes — lower risk, high value, and where these shine.
Implemented in v1 (`domain/behaviour/body.py`, `summarize_body`):
- reset-aware differencing of each counter into per-window rates (`_deltas`): a
  negative jump beyond `RESET_DROP_FRAC` of the prior value is read as a midnight
  rollover, never a negative delta;
- **coverage honesty**: absent windows are *not* counted as zero — `coverage` tracks
  worn-vs-not so a phone-on-the-charger day isn't shown as "totally still";
- per-day totals, a primary-signal **rhythm heatmap** (when you move), and a
  **per-activity cross-tab** ("steps during cooking vs movie") that quietly validates
  the activity labels and flags mislabels (a "sleeping" window with 800 steps is
  suspicious).
The route reads bound `Role.STEPS` sensors via `tsdb.read_raw(..., freq="30m")` over
the display range; `body` is `null` when nothing is bound. UI: a "Body activity" card,
hidden unless a counter is bound.

**Charging state (DONE, June 2026).** HA also exposes a phone charging entity, which
sharpens both layers:
- *Body coverage* now splits three ways — **worn** (data, not charging) /
  **charging** (docked; steps≈0 is EXPECTED, so these windows are dropped from the
  rate deltas, not read as "still") / **absent** (no data, not charging). The UI shows
  a worn/charging/away bar so a phone-on-the-charger night never looks sedentary.
  (`body.py`: `_charge_buckets`/`_truthy`; route reads any binding whose entity/name
  mentions "charg".)
- *Reliability gate* — `_awake_evidence` (facts.py) now has two tiers: HARD evidence
  (lights, media, **real step movement** `{steps}_delta > STEP_AWAKE_STEPS`) always
  contradicts "asleep"; the SOFT daytime prior is **cancelled when the phone is
  charging AND still** (`_charging_rest`: a "charg" column, or a rising `{batt}_delta`).
  So a daytime nap with a parked phone no longer dings the bed sensor, while steps
  climbing during "asleep" correctly does. This is how a flaky bed sensor can EARN
  fact status with wearable corroboration. Backward-compatible: with no steps/charging
  columns it reduces to the old daytime-OR-lights-OR-media behaviour.

**Still open / later.** A dedicated binding picker in the wizard (today: bind via the
normal binding flow with role=steps, and a binary charging sensor by any role); per-
person attribution requires one wearable per person (a shared phone can't be
attributed); active-vs-sedentary minutes + a trend callout on them; exact
(non-quantised) edges.

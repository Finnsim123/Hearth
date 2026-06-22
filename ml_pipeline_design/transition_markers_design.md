# Hearth — transition markers (events vs activities)

Status: design / brainstorm, June 2026. Discovery now surfaces patterns like "coffee
machine on in the morning" or "alarm fires" — but the only way to classify a pattern
is as an *activity you do*. Those are not activities; they're **moments that mark a
change of state** (asleep → awake). Forcing them into the activity taxonomy is wrong,
and throws away their real value. This proposes a first-class **transition marker**.

## 1. Two different kinds of thing

- **State / activity** (what we model today): a *durative* thing the person IS doing —
  sleeping, cooking, watching a movie. Fills 30-min windows; the classifier predicts
  one per window.
- **Event / transition marker** (missing): a near-*instantaneous*, often appliance- or
  automation-driven signal that marks the **boundary** between states — alarm fires,
  coffee machine kicks on, bathroom light at 07:00, garage door at 18:10. It has no
  sensible duration; its value is the *timing of the transition*, not a label for a span.

## 2. Why forcing an event to be an activity is wrong
1. **No duration** — you don't spend 30 min "alarm clock"; quantising it to a window
   misrepresents it.
2. **Softmax pollution** — added as a class, it competes with real activities and steals
   probability mass, hurting the very predictions it should help.
3. **Dishonest labelling** — the user genuinely can't mark spans as "coffee machine."
4. **Throws away the signal** — the information is "a transition is happening *now*,"
   which the per-window activity classifier discards. That's exactly what the HMM /
   forward-filter layer wants and the classifier can't use.

## 3. The model: a `Marker`
A marker is the *dynamic* cousin of a foundational fact. A gating fact says "this window
IS away/asleep" (a state); a marker says "right now you are **changing** from asleep to
awake" (a boundary).

```
Marker:
  slug, name              "wake-up", "morning coffee"
  from_state: str | None  e.g. "asleep"  (None = any)
  to_state: str           e.g. "home"    (an existing activity — no new taxonomy)
  signal:                 the discovered cluster id, or a binding+condition
  excluded_from_model: true   # NEVER a classifier label
```

Crucially `excluded_from_model` keeps it out of the label set (reusing the spirit of the
`model_excluded` flag we already have for the discovery⟂model split). It is consumed by
the **transition layer**, not the softmax.

## 4. What a marker feeds (the payoff)
1. **Transition prior (forward filter).** The predictor already conditions on a learned,
   daypart-keyed transition matrix (`smoothing.transition_filter`, `transitions.{pid}`).
   Today that prior is *stationary* — "around 7am, asleep→home is plausible." A marker
   makes it *observed and time-localized*: when the marker fires in a window, sharply
   boost `P(from → to)` and suppress the `from` self-loop for that step. The forward
   filter then switches **cleanly at the right window** instead of lagging — the classic
   fix for HAR transition-boundary error, now driven by a semantic appliance event.
2. **Boundary timing for the published state.** The smoother flips the published
   activity at the marker, so "awake/home" starts when the coffee machine does, not 30–60
   min later when ambient signal finally shifts.
3. **Behaviour dashboard.** Render markers as **points/flags on the ribbon** ("☕ 07:05
   — woke up"), never as coloured spans. They become precise **wake/bed anchors** for the
   consistency panel (often more reliable than a flaky bed sensor) and natural nodes in
   the sequences view.
4. **Reliability corroboration.** A consistent alarm/coffee event is cheap, strong
   evidence for the asleep→awake transition — the same corroboration role charging plays
   for sleep. It can help a weak bed sensor, and contradict a stuck one (coffee's on but
   "still asleep"? the sensor's wrong).

## 5. Discovery UX — ask the right question
When a cluster is surfaced for naming, add one branch:

> *Is this something you **do**, or a **moment that marks a change**?*
> · "Something I do" → name it as an activity (today's path).
> · "A moment of change" → pick from-state → to-state ("when I go from **asleep** to
>   **home**"). It becomes a marker, not an activity.

**Auto-suggest the right kind.** A marker-like cluster is statistically distinct: short
median dwell + high time-of-day concentration + tight to one or two appliance signals.
When discovery sees that profile, pre-select "moment of change" and say why ("this is
brief and happens at a consistent time — looks like a transition, not an activity").
This advances the earlier goal of *intuitively asking for input only when it helps*.

## 6. Why this is mostly wiring, not new ML
- The **transition matrix + forward filter already exist** and are already applied
  per-window in the predictor — markers just inject a time-localized prior.
- The **discovery clusterer already finds these patterns** (that's the user's
  observation); we only add a classification branch + a dwell/time-concentration
  heuristic to route them.
- The **foundational-fact / reliability machinery** gives us the corroboration and the
  "excluded from model" precedent for free.
- The **Behaviour timeline** already merges segments; markers are a thin point overlay.

## 7. Build plan (incremental)
1. **Schema + store**: a `Marker` (settings-backed, like foundational facts) with
   from/to/signal/excluded_from_model; helpers load/save.
2. **Discovery branch**: cluster-naming UI gains the activity-vs-marker choice + the
   auto-suggest heuristic (dwell + time concentration) from cluster stats.
3. **Predictor hook**: when a marker's signal is present in a window, apply a sharpened
   transition prior (boost from→to, damp the self-loop) before/with `transition_filter`.
   Markers never enter the label set.
4. **Behaviour**: render markers as flags on the ribbon; feed wake/bed anchors into the
   consistency panel.
5. **Reliability (optional)**: use a marker as a corroboration series for its transition.

## 7b. Status — IMPLEMENTED (June 2026)
- **Domain** `domain/markers.py`: `Marker` (settings-backed, `excluded_from_model`),
  load/save/`markers_for`, `binding_from_feature`, `marker_fired`, `apply_marker_prior`
  (boost from→to, damp the self-loop, renormalise), `looks_like_marker` heuristic. Tested.
- **Predictor**: in the transition block, after the learned `transition_filter`, fired
  markers re-weight the row toward `to_state`; markers never enter the label set. Applies
  even before a transition matrix exists. (Decision: one-sided `from=None` supported.)
- **API**: `markers_routes` CRUD + candidate sensors/activities; `POST
  /clusters/{id}/marker` classifies a discovered pattern as a marker bound to its
  dominant signal (`signature[0]`), emits no activity labels, records a timeline event.
- **UI (consistent across the model surface)**: a `TransitionMarkers` card in
  Settings → Model (next to Foundational facts; manual add by sensor + from→to); the
  Patterns card gains an *"Something I do / A moment of change"* toggle that
  auto-pre-selects marker mode for brief, time-locked clusters; Behaviour's Today ribbon
  shows marker moments as flags.
- **Deferred** (decisions 2): auto-promoting a marker to a reliability-gated
  corroborator for sleep/away — left as a future suggestion, not auto-wired.

## 7c. Lead / lag — markers that don't coincide with the transition (June 2026)

Real markers rarely fire *exactly* at the transition. Two examples: the coffee machine
runs ~30 min **before** waking; the lights pop on ~1 min **before** arriving home.

**Does it matter? Only when the offset crosses a 30-min window boundary.**
- *Lights, −1 min*: the signal and the real arrival fall in the **same** window, so the
  boost already lands on the right window. No action needed — sub-window offsets are free.
- *Coffee, −30 min*: a full window early. v1 boosts the *fire* window, so it would mark
  you awake at 06:30 while you're still in your last sleep window → ~30 min mislabelled,
  and the wake-time anchor reads 30 min early. This one needs handling.

**Two real risks with a lead marker**
1. *Timing* — the state flips early (above).
2. *Causality* — a lead signal is often an **automation/timer** (coffee on a schedule)
   that fires *regardless* of whether you actually wake. Treated naively it invents a
   transition on days you sleep in.

**Design**
- **Offset** (`lead_min`, signed): apply the boost at the window nearest
  `fire_time + lead_min`, not the fire window. For coffee, +30 lands it on the real wake
  window; for lights, ~0 ⇒ unchanged. Sub-window offsets round to 0 → no-op (so the
  lights case stays free).
- **Learn it, don't make the user measure.** Cross-correlate each marker's fire times
  against the actual from→to boundaries in the published/confirmed timeline: take the
  **modal lag** and its **spread**. Auto-set `lead_min`; surface it ("your coffee fires
  ~30 min before you actually wake — I account for that"). Manual override allowed.
- **Reliability gate (reuse the pattern).** Score a marker by hit-rate (how often the
  fire is actually followed by the transition within the expected window) and lag
  variance. High variance / low hit-rate → demote to a *hint* (small boost), not a
  committing flip — exactly the fact-vs-hint gate we use for sleep sensors.
- **Corroboration for lead markers.** A lead marker *arms* an expected transition; only
  **commit** the flip if corroborating evidence shows up around `fire + lead` (movement,
  lights, steps, or the model already trending). Coffee on + no motion for an hour ⇒ the
  timer fired but you didn't wake ⇒ suppress. This kills the fixed-timer false-positive.

Net: offset fixes the *timing*; the reliability gate + corroboration fix the *causality*.
Coincident markers (lights) are unaffected; anticipatory ones (coffee) become correct.

## 8. Open questions (for you)
1. Should a marker require an explicit `from`→`to`, or also support a one-sided
   "wake-up" that just anchors a time without asserting the prior state? (Lean: allow
   `from=None` = "anchor the transition into `to`".)
2. Auto-promote markers to **reliability-gated corroborators** for sleep/away, or keep
   that manual? (Lean: surface it as a suggestion, don't auto-wire.)
3. Do markers belong only to discovery, or should the user be able to define one by hand
   from a sensor (e.g. "this alarm entity = my wake-up")? (Lean: both — a manual path is
   cheap and very legible.)

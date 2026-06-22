# Hearth — honest capability ("tell me when it won't work")

Status: design / brainstorm, June 2026. The worry: the UI is relentlessly positive
("analysing… predicting…") even when the model is genuinely bad — too little data, or
sensors that can't separate the activities. The user is "held a fool." We want a system
that, when something genuinely won't work, says so plainly and tells you what to do
("with these sensors I can't tell cooking from eating — add a stove power sensor"). To
do that we must first **know precisely what works and what doesn't**.

## 1. The root problem: the UI conflates *trying* with *succeeding*
"Analysing", "processing", "predicting" describe *activity*, not *competence*. A model
can be busily predicting and be 38% accurate. Honesty = never show an action without its
quality. Everything below follows from that one rule.

## 2. We already measure enough — we just don't judge or surface it
Per promoted model we already compute: `accuracy_gold` + Wilson CI, `n_gold`,
`validation_status` (provisional/validated at ≥30 confirmed), `flat_baseline` (does the
model beat a dumb majority/flat model?), `per_class` precision/recall/F1/support, the
`confusion` matrix, calibration ECE, slices, drift. The coverage advisor already finds
ghost rooms and confusable pairs lacking a discriminating sensor. None of it is turned
into a verdict or shown honestly.

## 3. A per-activity capability verdict (the "know what works" core)
A pure function grades **each activity** for a person into one honest tier:

- **reliable** — validated, F1 ≥ bar, beats the flat baseline, not heavily confused.
  "I can tell when you're cooking."
- **learning** — not enough data yet (provisional / low `n_gold`/support). Temporary;
  show progress, don't pretend. "Still learning cooking — 12/30 checks."
- **unreliable** — *enough* data but it doesn't work. Two sub-reasons, because the
  remedy differs:
  - **confused_with X** — the confusion matrix shows it blurs with another activity
    (cooking↔eating). Remedy: add a sensor that separates them, or merge the two.
  - **weak_signal** — low F1 not from one pair; the signal just isn't there. Remedy:
    add sensors in its room.
- **blind** — the activity's room has no usable sensor at all (coverage ghost_room).
  Can't even attempt. "I'm blind in the kitchen — I can't see cooking without a sensor."

Plus an **overall** per-person read: e.g. "Reliable: home, away, asleep. Can't yet:
cooking, eating (confused — same room, one sensor)." The honest headline.

**Remedy generation** combines the confusion pair with the coverage advisor's room/role
gap into one concrete sentence: *"I keep mixing cooking and eating — both happen in the
kitchen, which only has motion. A power sensor on the stove would separate them."*

## 4. Stop the false positivity — honest surfaces
1. **"What I can and can't do" panel** (Models page): the per-activity tiers with plain
   reasons and remedies. The deliverable the user asked for.
2. **Predictions badged by reliability.** We already tag basis (known/inferred); add the
   tier. An `unreliable` activity prediction is shown as a *hedged guess* ("possibly
   cooking — not reliable yet"), dimmed, never as a confident statement. `blind`/no-model
   → don't assert at all.
3. **Buddy / advisories** (reuse what we built): when an activity is unreliable or blind,
   raise an advisory carrying the remedy. The buddy's live line tells the truth —
   "watching · home/away reliable, cooking unsure" — not a blanket "predicting".
4. **Honest holding state.** If the model can't do anything beyond facts (only home/away
   work), the dashboard says so plainly instead of a confident activity ribbon.
5. **Tone of progress copy.** "Analysing/processing" during setup is fine — it IS. Once
   live, replace the unconditional "predicting" with the quality-qualified truth.

## 5. Why this is mostly judgement + surfacing, not new ML
The metrics, confusion, coverage advisor, provisional/validated status, basis tags, and
the advisory/buddy channel all exist. The new parts are: one pure verdict engine that
aggregates them with explicit thresholds, and honest rendering that pairs every claim
with its quality.

## 6. Build order
1. `domain/capability.py` — pure `assess_capability(...) -> CapabilityReport`
   (per-activity tier + reason + remedy + overall). Thresholded, tested. **This is
   "know what works".**
2. `GET /api/capability?person=` + fold poor verdicts into the advisory system (with
   remedies) so the buddy/Activity already surface them.
3. "What I can and can't do" panel (Models page).
4. Reliability badges on predictions (dashboard + behaviour) + the honest holding state.
5. Tone pass on buddy/insight copy.

## 6b. Status — IMPLEMENTED (June 2026), first slice
Decisions taken: label-and-dim (not hide), **balanced** thresholds, panel on Models.
- `domain/capability.py :: assess_capability` — per-activity tiers
  (reliable/learning/unreliable[confused_with|weak]/blind) + reason + remedy + overall;
  thresholds F1 0.70/0.50, confusion 0.40, gold ≥30, support ≥10. Tested (6).
- `GET /api/capability?person=` aggregates the promoted root model's metrics +
  `gaps_from_home`.
- Folded into the advisory scan (`advisory_scan._capability`): an honest info advisory
  with the remedy when anything is unreliable/blind → shows on the buddy + Activity.
- "What I can and can't do" panel on the Models page: per-activity tier, plain reason,
  and the concrete fix.
- **Deferred to the next slice:** reliability badges/dimming on the dashboard +
  behaviour ribbons (label-and-dim per the decision), the blunt holding state when only
  facts work, and the buddy live-line tone pass. The verdict engine + API are ready for
  them to consume.

## 7. Decisions (for you)
1. **Reliability bar** — strict or lenient? Lean: `reliable` = validated AND F1 ≥ 0.70
   AND beats flat; `unreliable` = (validated OR `n_gold` ≥ 30) AND (F1 < 0.50 OR loses to
   flat OR a confusion pair > 0.40); else `learning`. (Tunable in one place.)
2. **How blunt with predictions** — label-and-dim unreliable ones, or hide them? Lean:
   label + dim (hiding loses information and feels like a cover-up).
3. **Where the panel lives** — Models page, or a dedicated "Honesty" view? Lean: Models
   (it's model truth), cross-linked from the buddy.

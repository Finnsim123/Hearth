# Hearth — UX Design: Model Insight & Control

Status: design proposal, June 2026. Read-only on the repo. Proposes UX, does not
implement. Scope: give the user **more insight** into the model and **more
control** over it, grounded in the state of the art for production-ML model
understanding and tweaking — adapted to Hearth's constraints (single household,
permanent small-label regime, local-first, glass-box ethos, a non-analyst primary
user who must stay calm but a power user who must be able to reach every lever).

Companion docs: `model_levers.md` (the knob catalog + basic/advanced/internal
split — this doc wires those into UI), `ml_correctness_audit.md` (F1–F6, now
implemented — several produced metrics this doc surfaces), `system_observability_
and_governor_design.md` (the Vitals/Governor page — system health, not model
insight; kept separate).

---

## 0. The core finding

Hearth already **computes** far more than it **shows**. The audit work (F1–F6)
added honest metrics that currently die in the registry JSON:

| Computed & stored | Where | Surfaced in UI? |
|---|---|---|
| `accuracy_gold` + CI (unbiased headline, F1) | `evaluate.py` | ❌ used only for the promotion gate |
| `calibration.brier` / `ece` (F4) | `evaluate.py` | ❌ |
| `flat_baseline` accuracy (F3) | `trainer.py` | ❌ |
| drift PSI report + 10-point trend (F5) | `drift.py`, `GET /drift` | ❌ no page consumes it |
| `importance_all` (full vector) | `trainer.py` | ⚠️ only top-15 shown |
| `excluded_features` | `trainer.py` | ❌ |
| per-window SHAP | `predictor.py` | ⚠️ top-3 chip only, not explorable |

And `model_levers.md` catalogs ~30 tuning levers; the UI exposes ~8. So the
single highest-leverage UX move is **not new ML** — it is surfacing what exists,
in the visual idioms the field has converged on, behind progressive disclosure.

The design below is organized as: **(1)** SOTA methods mapped to Hearth, **(2)**
insight surfaces, **(3)** control/settings surfaces, **(4)** cross-cutting
principles, **(5)** a prioritized roadmap.

---

## 1. State of the art, mapped to Hearth

The production-ML-understanding field has three established pillars. Each has a
canonical toolset; Hearth should borrow the *idioms*, not the infrastructure
(local-first → no Arize/Fiddler SaaS, no second database).

| Pillar | SOTA exemplars | The idiom to borrow | Hearth surface |
|---|---|---|---|
| **Observability / monitoring** | Evidently AI, NannyML, Arize, Fiddler, WhyLabs | drift panels (PSI/KL per feature over time), performance-over-time, data-integrity checks, "what to track / when to act" | a **Drift & Health** view fed by `/drift` (F5) + performance trend already on Models |
| **Explainability** | SHAP, Google PAIR **What-If Tool**, **LIT**, PDP/ICE, captum | global importance + **local per-instance** explanation + **counterfactual probing** ("change this, watch the prediction move") | extend the Dashboard "Based on" chip into an explorable **Why panel**; add a **What-If probe** |
| **Documentation / governance** | **Model Cards** (Mitchell 2019), **IBM AI FactSheets**, MLflow Model Registry UI | one standardized, shareable "nutrition label" per model; champion-challenger version diff | an auto-generated **Model Card** page + a **version diff** on the existing history table |

Two cross-cutting research currents inform *how* to present this:

- **Slice / cohort analysis** (Google SliceFinder; "Where Does My Model
  Underperform?", 2023). Aggregate accuracy hides failure pockets. Production UIs
  now lead with *per-slice* performance. Hearth's natural slices are free:
  **per-activity** (have it), **per-daypart**, **per-weekday**, **per-person**.
- **Human-in-the-loop as a primary UX strategy** (not an afterthought). Hearth is
  already HITL (the Inbox/asking loop). The SOTA move is to **show the user the
  value of their labels** — label-efficiency curves, "answering this question is
  worth ~X" — which raises answer rates and trust.
- **Trust calibration / appropriate reliance.** The literature is consistent:
  showing uncertainty honestly (CIs, abstention, "provisional") produces *better*
  human reliance than a single confident number. Hearth's instincts here are
  already right (Wilson CI, provisional/validated badge, abstain). The job is to
  extend that honesty to every surfaced number.

---

## 2. Insight surfaces (see more)

Ordered by value. Each names the SOTA idiom, the Hearth data that already exists,
and the concrete UI.

### 2.1 Make the honest number the headline (F1) — **highest value, trivial**
Today the Models page leads with `accuracy_confirmed`, which the audit showed is
*pessimistically biased* (uncertainty-sampled). `accuracy_gold` is the unbiased
estimate and already drives promotion.
- **UI:** lead with **gold accuracy + CI** as the hero number, labelled "measured
  on random spot-checks" with a tooltip explaining why it's the fair one. Show
  `accuracy_confirmed` beside it as "on the tricky moments we asked about"
  (always lower — frame it so that's expected, not alarming). Gate the gold number
  behind `n_gold ≥ 30`; below that show "still gathering spot-checks (N/30)".
- **Why:** without this, the headline contradicts the number the system actually
  trusts. This is the cheapest credibility win in the product.

### 2.2 Reliability diagram + Brier/ECE (F4) — calibration made visible
Calibration is now measured (`metrics.calibration`) but invisible. The canonical
visual is the **reliability diagram** (predicted-confidence bins vs observed
accuracy; the diagonal is perfect).
- **UI:** on Models → details, a small reliability curve + the ECE/Brier numbers,
  with one plain-language line: *"When Hearth says 80% it's right about 78% of
  the time."* This directly justifies the abstain slider and the asking threshold
  — both consume confidence, so proving confidence is honest matters.

### 2.3 Drift & Health view (F5) — the Evidently/NannyML idiom
`/drift` returns per-feature PSI, a drifted list, `max_psi`, and a trend; nothing
renders it.
- **UI:** a **Drift** card/section (likely on Sensors, since drift is per-sensor-
  feature, or a small panel on Models):
  - the **trend sparkline** of `max_psi` over the last ~10 runs (already stored),
    with the 0.2 "investigate" line drawn;
  - a sorted list of drifted features with a per-feature mini before/after
    distribution (PSI is literally a binned-histogram comparison — show the two
    histograms);
  - the plain-language health issue Hearth already raises ("your home's signals
    have shifted") with the **Train now** CTA;
  - the **`drift.auto_retrain`** toggle (the setting exists; expose it).
- **Why:** this is the SOTA "when to act" surface and closes the F5 loop visually.

### 2.4 Slice / cohort performance — find where it fails
Per-class P/R/F1 exists; per-*context* does not. Activities cluster by time, so
the model can be great at 8pm and poor at 3pm and the aggregate hides it.
- **UI:** a small matrix/heatmap of accuracy by **daypart × activity** (and a
  weekday toggle, and per-person in multi-member homes). Red cells are exactly
  where to look. This is the SliceFinder idiom at zero modelling cost — the
  windows are already labelled with time.
- **Backend:** needs `evaluate_model` to also bucket the val set by daypart/
  weekday (cheap; the timestamps are in the index).

### 2.5 What-If / counterfactual probe — the trust-builder
Google's What-If Tool / LIT let a user pick an instance, see the explanation, and
**perturb inputs to watch the prediction change**. Hearth has per-window SHAP and
a live model — this is within reach and is the single most *convincing* insight
surface for a skeptical user.
- **UI:** pick a window (from the Dashboard heatmap or a time picker) →
  - show prediction, calibrated confidence, top SHAP signals (the "Why panel",
    an expansion of today's chip);
  - **sliders for the top features** ("what if the sofa had been empty?" / "what
    if CO₂ were lower?") that re-score live and show the prediction moving.
- **Why:** turns the glass-box claim into something the user can *operate*. Also a
  debugging tool ("it thinks movie because the TV-power feature is stuck high").
- **Cost:** medium — needs a `POST /predict/probe` that scores an edited feature
  row through the promoted model; the model is already loadable.

### 2.6 Model Card — the shareable nutrition label
Mitchell-2019 Model Cards / IBM FactSheets standardize "what is this model, on
what data, how well, with what limits." Hearth has every field already.
- **UI:** a generated, printable **Model Card** per promoted model: training
  window + #windows, label provenance breakdown (`label_counts`), gold/confirmed
  accuracy + CI, calibration, per-class, **flat-baseline comparison** (F3 —
  "hierarchy 0.81 vs flat 0.78, the hierarchy earns its keep" or the reverse),
  excluded features + why, intended use + known limits (canned, honest copy).
- **Why:** consolidates scattered numbers into one honest artifact; matches the
  glass-box promise and is genuinely nice to screenshot/share.

### 2.7 Champion-challenger version diff
The history table exists; the SOTA registry UX (MLflow/W&B) is a **diff** between
the live model and a candidate, with the **promotion decision explained**.
- **UI:** "vN+1 vs live": deltas on gold/confirmed/calibration/flat-baseline, and
  a one-line **why the gate promoted or rejected** it (the gate logic is known —
  surface it: *"rejected: gold-accuracy CI lower bound 0.71 < live 0.74 − margin"*).
  Wire the existing `/models/rollback` to a button here.

### 2.8 Active-learning value (HITL insight)
Show the payoff of answering questions: a curve of **gold accuracy vs #labels**
over time, and on each Inbox question a small "this one helps because the model is
unsure here" vs "spot-check" tag (the `ask_reason` field from F1 already
distinguishes them).
- **Why:** the literature shows surfacing label value increases response rate; it
  also explains *why* Hearth asks what it asks, which users find opaque otherwise.

### 2.9 Global feature effect (PDP/ICE) — *optional, advanced*
SHAP gives attribution; PDP/ICE give *shape* ("more CO₂ → more likely cooking, up
to a point"). Nice-to-have on an advanced Explain tab; lower priority than local
What-If for a home user.

---

## 3. Control surfaces (tweak more)

`model_levers.md` already did the hard thinking: every knob is classified
basic / advanced / internal, with safe defaults. The UX task is to **implement
that disclosure** and add a few SOTA interaction patterns. The guiding rule from
the levers doc holds: **basic is intentionally tiny; everything else is advanced
or internal.**

### 3.1 The tiny basic tier (most users see only this)
Framed in plain language, no jargon, each with a one-line consequence:
- **"Treat rare activities as important"** (on) → `class_weight` (already on;
  expose as a toggle).
- **"How sure before Hearth commits?"** slider → abstain threshold (partly exists
  via output-policy; unify it here) with a **live preview** (see 3.3).
- **Window length** 15 / 30 / 60 min → with "changes how Hearth chunks your day;
  needs a retrain."
- **Smoothing strength** low / med / high → maps to hysteresis `k` + transition
  mix; never show `k` itself.

### 3.2 The advanced disclosure (power user, behind a toggle)
Wire the levers-doc "advanced" set, grouped as the catalog groups them: model
family (exists) + RF `min_samples_leaf`/`max_depth`/`max_features`; validation
mode (**temporal holdout / leave-one-day-out**); `promotion_margin`;
`recency_half_life_days`; tune cadence; calibration method (**isotonic / sigmoid**
— levers §6 recommends sigmoid below some n); per-stage post-process toggles
(transition filter / hysteresis / evidence cap on/off for debugging);
`drift.auto_retrain`. All already exist as `TrainingConfig`/settings keys or are
named in the catalog — this is plumbing + disclosure, not new ML.

### 3.3 "Set with preview" — the counterfactual-settings pattern
The strongest modern settings UX doesn't just accept a value, it **previews the
consequence** before save (this is the What-If idea applied to configuration):
- the **abstain slider** previews the coverage/precision trade live: *"at this
  threshold, Hearth commits on ~82% of windows and is right ~94% of those; the
  rest become 'unknown' or a question."* (computable from the held-out val probs).
- changing **window length / model family / granularity** shows a clear
  **"requires a retrain to take effect"** state and offers **Train now**, rather
  than silently drifting config and model out of sync.
- **smoothing strength** previews on a recent day: same ribbon, raw vs smoothed,
  so the user sees flicker-vs-lag with their own data.

### 3.4 Quiet-compute hours (control that bridges to the Governor doc)
From the observability design: let heavy retrains run only when away/asleep/cheap-
electricity. A control here, enforced by the scheduler/governor.

### 3.5 Guardrails (don't let a knob break honesty)
Per the levers doc: validation-as-shuffled-CV, training-overlap, and
"headline = confirmed/gold only" are **internal and locked** — never exposed,
because exposing them invites the exact leakage bug the audit praised Hearth for
avoiding. Advanced ≠ dangerous; the dangerous ones stay internal.

---

## 4. Cross-cutting UX principles

1. **Progressive disclosure (basic → advanced → internal).** Already the levers-doc
   spine; apply it uniformly. A calm default surface; one "Advanced" disclosure;
   never a wall of sliders. This is the dominant pattern in production ML config
   UIs precisely because most users should touch nothing.
2. **Every number carries its uncertainty.** CIs, `n=`, provisional/validated.
   Trust-calibration research is clear that honest uncertainty beats false
   precision for *appropriate* reliance. Hearth already does this; extend it to
   the newly surfaced numbers (gold CI, ECE, PSI bands).
3. **Plain-language consequence on every control.** No `min_samples_leaf` without
   "higher = smoother, less overfit." The levers doc supplies this copy.
4. **Local + global, paired.** SHAP-per-window (local, the Why panel / What-If)
   *and* importances/PDP (global, the Model Card) — the field treats these as
   complementary, not either/or.
5. **The buddy as the natural-language explainer.** Hearth's buddy is the chance
   to do what LIT/What-If do with GUIs, conversationally: *"why did you think I
   was cooking at 3pm?"* → pull the window's SHAP + calibrated confidence and
   explain; *"what changed since last week?"* → read the drift report. This is the
   most differentiated insight surface Hearth can build and it reuses an existing
   component.
6. **Insight links to action.** Evidently/Fiddler's "what to track / when to act":
   a drift flag → Train now; a red slice → a targeted asking burst for that
   daypart; a failed promotion → rollback. Never a dead-end chart.

---

## 5. Prioritized roadmap

Ranked by value-to-effort, leaning on the fact that most of this is *surfacing*
existing data.

1. **Gold accuracy as headline + provisional gating** (2.1) — trivial, fixes a
   live credibility contradiction.
2. **Reliability diagram + Brier/ECE** (2.2) — small; justifies the whole
   confidence/abstain/ask machinery.
3. **Drift & Health view + auto-retrain toggle** (2.3) — small/medium; closes the
   F5 loop visually; the canonical observability surface.
4. **Model Card page** (2.6) — small/medium; consolidates F1/F3/F4 numbers into
   one honest artifact; high perceived polish.
5. **Slice/cohort heatmap** (2.4) — medium (needs daypart bucketing in
   `evaluate_model`); finds failure pockets aggregates hide.
6. **Basic settings tier + "set with preview"** (3.1, 3.3) — medium; the
   abstain-preview alone materially improves the most-used knob.
7. **Advanced disclosure wiring** (3.2) — medium; mostly plumbing existing config.
8. **What-If probe + Why panel** (2.5) — medium/large but the standout
   trust-builder; needs a probe endpoint.
9. **Version diff + gate explanation, rollback button** (2.7) — medium.
10. **Buddy-as-explainer** (4.5) — medium; reuses the buddy, high differentiation.
11. **Active-learning value curves** (2.8), **PDP/ICE** (2.9) — later polish.

None of this adds a second database or a cloud dependency; it is the
OpenTelemetry-/Evidently-/What-If-shaped *idioms* rendered in Hearth's own calm
visual language, fed almost entirely by metrics the pipeline already produces.

---

## Sources

- Evidently AI — open-source ML/LLM observability: https://www.evidentlyai.com/
- Evidently — data drift, detect & handle: https://www.evidentlyai.com/ml-in-production/data-drift
- Fiddler — ML observability glossary: https://docs.fiddler.ai/reference/glossary/ml-observability
- Comparison of monitoring tools (Evidently / Alibi Detect / NannyML / WhyLabs / Fiddler): https://medium.com/@tanish.kandivlikar1412/comprehensive-comparison-of-ml-model-monitoring-tools-evidently-ai-alibi-detect-nannyml-a016d7dd8219
- The What-If Tool: Interactive Probing of ML Models (Wexler et al.): https://arxiv.org/pdf/1907.04135
- Where Does My Model Underperform? Human Evaluation of Slice Discovery: https://arxiv.org/pdf/2306.08167
- A Visual Tool for Interactive Model Explanation using Sensitivity Analysis: https://arxiv.org/pdf/2508.04269
- Model Monitoring in Production — what to track and when to act: https://sentryml.com/posts/model-monitoring/

# ML Levers Catalog (Step 4)

Status: catalog, June 2026. Read only on the repo. Propose, do not implement.
Purpose: enumerate every knob that affects the classifier, grouped, with for each: what it does, its accuracy implication, its reliability and overfitting implication, a sensible default, and whether it belongs in the UI as basic or advanced. The catalog ends with a recommended first deployment configuration.

Two framing rules I follow throughout, both grounded in Hearth's own context:

- Hearth lives in a permanent small label regime (about 48 windows per person per day, an asking budget of 5 to 10 per day, RESEARCH.md P1). Almost every accuracy lever is also an overfitting lever here, because there is little data to absorb a bad setting. So the reliability column matters as much as the accuracy column, and defaults lean conservative.
- Windowing and validation are treated as first class, ahead of model family and hyperparameters, because in HAR they dominate (Cook and Krishnan; RESEARCH.md P4), and because a leaked validation split makes every other lever's tuning meaningless.

Where a value is a judgment rather than a sourced fact, it is labeled (judgment).

A note on the UI columns: "basic" means it appears in the main settings flow a non analyst will see; "advanced" means it sits behind an Advanced disclosure on the Models or Settings page; "internal" means it should be a versioned config or auto chosen, not a user knob, because exposing it does more harm than good. The Step 5 spec wires these in.

---

## Group 1: Model family

The estimator that maps a feature row to an activity. Hearth ships RandomForest today (`estimators.py`) behind a clean `Estimator` port (ADR-9), so swapping is an intended extension.

### Gradient boosted trees (the tabular default: XGBoost, LightGBM, CatBoost)

- What it does: sequential ensemble of shallow trees, each correcting the last. The modern default for tabular data and the standard successor to RF.
- Accuracy: typically the strongest tabular learner; usually beats RF by a few points when tuned, and handles the mixed boolean plus continuous feature soup Hearth produces well. On small data the margin over RF shrinks.
- Reliability and overfitting: more sensitive to hyperparameters than RF; easy to overfit a few hundred windows if learning_rate and depth are not controlled. Needs early stopping on a temporal validation split to be safe at Hearth's label counts (judgment).
- Default: not the day one default. Offer as the second estimator once a home has accumulated labels.
- UI: advanced (model family selector on the Models page).

### Random forest (current baseline)

- What it does: bagged ensemble of deep decorrelated trees, majority vote. Hearth's current estimator, `class_weight=balanced`, 300 trees, `min_samples_leaf=5`.
- Accuracy: strong and stable on small tabular data; rarely the absolute best but rarely bad. Robust to uninformative features (Grinsztajn 2022), which matters because Hearth's funnel cannot perfectly remove them.
- Reliability and overfitting: the most forgiving common model; deep trees plus bagging plus a leaf size floor resist overfitting without much tuning. This is exactly why it is the right cold start default (judgment, and it matches the prototype's validated choice).
- Default: yes, the day one default. Keep it.
- UI: basic (it is the default; the selector to change it is advanced).

### Logistic regression (the honest baseline)

- What it does: linear model over the features, one versus rest for multiclass.
- Accuracy: usually the weakest on this nonlinear problem, but a crucial sanity floor: if the RF cannot beat logistic regression by a clear margin, the features or labels are the problem, not the model.
- Reliability and overfitting: with L2 regularization, extremely stable and hard to overfit; fully interpretable coefficients. Best calibrated of the bunmodified models.
- Default: not the predictor, but run it automatically as a reported baseline so the Models page can show "RF 0.81 vs logistic 0.68" (judgment: this is cheap honesty and directly serves the glass box promise).
- UI: internal (auto run baseline), shown as a number, not a knob.

### Shallow MLP

- What it does: a small fully connected neural net on the feature row.
- Accuracy: rarely beats GBT or RF on small tabular data; needs more data than Hearth usually has to justify itself.
- Reliability and overfitting: high overfit risk at Hearth's label counts; needs careful regularization and is not interpretable. SHAP works but is slower.
- Default: no.
- UI: advanced at most; (judgment) consider omitting until there is evidence it helps a real home.

### Sequence models on raw windows: 1D CNN, TCN, LSTM, Transformer

- What they do: instead of consuming Hearth's aggregated feature row, these consume the raw or lightly processed time series within the window and learn temporal structure directly.
- Accuracy: can exceed feature based models when (a) there is a lot of labeled data, and (b) fine grained intra window dynamics matter. In HAR they shine on high frequency wearable data, less so on sparse ambient home sensors.
- Reliability and overfitting: severe overfit risk in Hearth's regime; they are data hungry and Hearth is label poor. They also break the glass box (harder to explain) and add GPU or heavy CPU cost, conflicting with the homelab sizing and the local first ethos.
- When warranted instead of the tabular path (the question you asked): only when all of these hold, which for ambient smart home HAR is rarely (judgment): the home has a large labeled set (thousands of confirmed windows, not dozens); the activities are distinguished by sub window temporal patterns that aggregation destroys; and you are willing to give up interpretability and pay the compute. For Hearth's ambient, label scarce, interpretability committed setting, the aggregated feature plus tree path is correct, and the self supervised embedding path (HEPA or DomusFM, RESEARCH.md section 4) is the better answer to "we want to use the raw stream" because it learns representations without labels and still feeds a cheap head. TCN is the most defensible of the four if you ever go raw (causal, no leakage, cheaper than a Transformer), with a Transformer justified only at data volumes a single home will not reach for years.
- Default: no, none of them, for v1.
- UI: advanced and feature flagged, tied to the Embedder port, not the day one estimator selector.

---

## Group 2: Windowing and segmentation (treated as first class)

These dominate HAR accuracy and currently are mostly hardcoded in Hearth (`WINDOW=30min`, stride 5 or 30, per role `window_min` in the registry, 1 minute resample). Making them levers is the single highest accuracy leverage change available, and it is also where leakage hides.

### Window length

- What it does: how much context each prediction summarizes. Hearth uses 30 minutes (schema `Literal["30m"]`, designed to widen).
- Accuracy: too short and slow activities (sleeping, a long movie) lose their signature; too long and short activities (cooking a quick meal) get smeared across windows and transitions blur. There is a per home, per activity sweet spot.
- Reliability and overfitting: longer windows produce fewer training rows (worse for label scarcity); shorter windows produce more rows but more correlated ones (worse for leakage). Both extremes hurt.
- Default: 30 minutes (keep). Allow 15 and 60 as alternatives. (judgment) the LLM can recommend per home at design time using the activity set and stats.
- UI: basic, but presented as a small choice (15 / 30 / 60 minutes) with plain language consequences, not a free integer.

### Stride and overlap

- What it does: how far the window moves between samples. Hearth uses 5 minutes at inference (dense, responsive) and 30 minutes at training (non overlapping, non leaky). This split is already correct and is a notable strength.
- Accuracy: smaller inference stride means faster reaction; training stride at the window length avoids overlap.
- Reliability and overfitting: this is a leakage lever. Overlapping training windows are the classic HAR mistake: adjacent windows share most of their raw data, so overlapping them in training inflates apparent accuracy and leaks into any random split. Hearth already avoids it (training stride equals window length). Do not expose a training overlap knob that could reintroduce it.
- Default: inference stride 5 minutes, training stride equal to window length (keep). 
- UI: inference stride advanced; training overlap internal and locked (judgment: exposing it invites the leakage bug).

### Resampling rate

- What it does: the common grid raw states are resampled to. Hearth uses 1 minute, last value wins, then role aware forward fill.
- Accuracy: finer grids capture quick events but enlarge the data and rarely help ambient sensors; 1 minute is a good ambient default.
- Reliability and overfitting: mostly a cost and memory lever; the forward fill limits per role matter more than the grid itself.
- Default: 1 minute (keep).
- UI: advanced.

### Event based versus periodic handling

- What it does: whether features are computed on a fixed time grid (periodic) or on event boundaries (segmentation). Hearth is periodic plus event dynamics features (`evt_count`, `evt_idle_minutes`), which is the CASAS aligned middle path.
- Accuracy: pure segmentation (change point detection, PELT/ruptures on the feature stream, RESEARCH.md P4) can sharpen transition windows, the exact place models err, and improves cluster quality. It is the most promising accuracy frontier after the basics.
- Reliability and overfitting: segmentation adds complexity and a new failure mode (bad boundaries), and makes windows variable length, which complicates the feature store. Treat as a later bet, not a v1 lever.
- Default: periodic plus event dynamics (keep). Segmentation off.
- UI: advanced and feature flagged, post v1.

---

## Group 3: Core hyperparameters per family

Hearth auto tunes monthly via RandomizedSearchCV with TimeSeriesSplit, f1_macro, caching per person (`estimators.py`, `trainer.py`). This is the right approach: most users should never touch these. They are catalogued for the advanced page and for the auto tuner's search space.

### Random forest (current)

- n_estimators (default 300): more trees means more stable, diminishing returns past a few hundred, only costs time. Accuracy: small positive then flat. Overfit: more trees do not overfit (bagging), so this is safe. UI: advanced.
- min_samples_leaf (default 5): the main RF regularizer. Higher means smoother, less overfit, the key knob at Hearth's label counts. Accuracy: a sweet spot; too high underfits. Overfit: higher is safer. UI: advanced (this is the one advanced users should reach for first).
- max_features (tuned: sqrt, 0.3, 0.5): features considered per split; controls tree decorrelation. Accuracy: moderate effect. Overfit: lower is more regularized. UI: advanced.
- max_depth (tuned: None, 12, 20): None lets trees grow fully (RF norm); capping helps only on very small or noisy data. UI: advanced.
- class_weight (fixed balanced): see Group 4. UI: basic toggle (see imbalance).

### Gradient boosted trees (when offered)

- n_estimators plus learning_rate: the central tradeoff; low learning_rate with more trees and early stopping is the safe recipe. Accuracy: high sensitivity. Overfit: high learning_rate plus many trees is the classic overfit; must pair with early stopping on a temporal split. UI: advanced, and (judgment) keep it behind auto tuning by default because it is the easiest model to misconfigure.
- max_depth (GBT, default small, 3 to 6): shallow is the GBT norm. Overfit: deeper overfits fast on small data. UI: advanced.
- min_child_weight: minimum summed instance weight per leaf; higher regularizes. UI: advanced.
- subsample and colsample_bytree (default ~0.8): row and column sampling per tree; both regularize and decorrelate. Accuracy: small positive. Overfit: lower is safer, too low underfits. UI: advanced.
- reg_alpha (L1) and reg_lambda (L2): explicit regularization; at Hearth's label counts lean higher than library defaults (judgment). UI: advanced.

### Tuning policy levers (apply to any family)

- Tune only above a data floor: Hearth uses TUNE_MIN_WINDOWS=500 (below it, tuning fits noise and defaults are safer). Keep. UI: internal.
- Tune cadence: monthly (TUNE_EVERY_DAYS=30), not every retrain. Keep; prevents chasing weekly noise. UI: advanced.
- Search via TimeSeriesSplit, never shuffled: non negotiable (see Group 5). UI: internal and locked.

---

## Group 4: Class imbalance handling

Rare but important activities (cooking once a day, RESEARCH.md data model section 5) are the reason this group matters; the headline metric must not be dominated by the easy majority (sleeping, away).

### Class weights (current: balanced)

- What it does: weights each class inversely to its frequency in the loss or split criterion. Hearth uses `class_weight=balanced` on the RF and `f1_macro` for tuning.
- Accuracy: improves recall on rare classes, the ones users care about, at a small cost to majority precision; macro F1 (which Hearth tunes on) is the right target.
- Reliability and overfitting: low risk; does not synthesize data, just reweights. The safest imbalance tool.
- Default: balanced (keep).
- UI: basic, framed as "treat rare activities as important" on/off, default on.

### Resampling (oversample minority / undersample majority, SMOTE)

- What it does: rebalances the training set by duplicating or synthesizing minority rows or dropping majority rows.
- Accuracy: can help, but SMOTE on time series is dangerous: synthetic windows between two real windows are not physically meaningful and interact badly with temporal structure and leakage. Random oversampling duplicates correlated windows, worsening leakage.
- Reliability and overfitting: high risk in Hearth's setting; SMOTE in particular can leak and fabricate (judgment). Prefer class weights.
- Default: off.
- UI: advanced, with a warning; (judgment) consider not exposing SMOTE at all and offering only mild random oversampling if anything.

### Focal loss

- What it does: down weights easy examples so training focuses on hard ones; a loss function option for gradient models, not RF.
- Accuracy: useful for GBT or neural models with extreme imbalance.
- Reliability and overfitting: another knob to misset; only relevant once a non RF estimator is in use.
- Default: off (and not applicable to the RF default).
- UI: advanced, only visible when a compatible estimator is selected.

### The abstain class as an imbalance tool

- Worth naming here: routing low confidence windows to an unknown or abstain class (Group 6) is also an imbalance and reliability tool, because it stops the model from being forced to guess a rare class it has barely seen. Cross referenced in Group 6.

---

## Group 5: Validation strategy (the prominent one)

This is the lever that determines whether every number the UI shows is true. Hearth already does this right in the trainer; the catalog states why, so it is never weakened, and so the new feature feedback loop (Step 3 section f) inherits it.

### Why random row wise cross validation is wrong here

Sensor windows are heavily temporally autocorrelated. With a 5 minute stride on 30 minute windows, consecutive windows share 25 of their 30 minutes of raw data, so they are near duplicates. Random k fold cross validation scatters these near duplicates across train and test folds, so the model is effectively tested on data almost identical to what it trained on. The result is an inflated accuracy that collapses in production. This is the single most common way HAR systems report 95 percent and deliver 70, and it is precisely the prototype's "90 percent" trap (RESEARCH.md lesson 3, P6). Random CV must never be used on this data.

### Required alternatives (use these)

- Temporal holdout (current trainer): train on everything before a cutoff, validate on the most recent block (`feats.index < cutoff`, VAL_DAYS=7). This respects the arrow of time and is the minimum bar. Keep as the default validation.
- Blocked or rolling origin cross validation (current tuner): TimeSeriesSplit, contiguous folds advancing through time, never shuffled. Use for hyperparameter search and for any feature accept/reject decision in the Step 3 loop.
- Leave one day out (recommended addition, judgment): hold out whole days, rotating, so each fold tests on a complete unseen day. This is the most honest estimator for daily routine data because activities cluster by day, and it gives a per day variance estimate that a single holdout cannot. It is more expensive (one fit per held out day) but cheap at Hearth's data sizes and RF speed. Recommend offering it as the rigorous validation mode on the Models page.

### Levers in this group

- Validation mode: temporal holdout (default, basic) / leave one day out (advanced, rigorous) / blocked CV (used internally by tuning, internal).
- Validation window length (VAL_DAYS, default 7): longer is a more stable estimate but leaves less for training; 7 days captures a full weekly cycle, a good default. UI: advanced.
- Headline metric scope: accuracy on confirmed labels only, with a Wilson interval (current, `evaluate.py`). This is the honesty lever and must stay locked; bootstrap agreement accuracy is reported separately and clearly named, never as the headline. UI: internal and locked; the distinction is shown to the user but not editable.
- Promotion gate margin (default 0.02 on CI overlap): how much worse a new model may be before it is rejected. Tighter means more stable but slower to adopt improvements. UI: advanced.

The cold start caveat from the Step 1 audit attaches here: when there are zero confirmed labels, the gate falls back to bootstrap agreement, which is circular. The validation lever cannot fix that alone; it needs the cold start policy decision (a minimum confirmed count before claiming validated, or an explicit provisional state). Flagged again because it is a validation honesty issue, not just a gate issue.

---

## Group 6: Prediction post processing

These sit between the raw classifier output and what the user and HA see. Hearth already implements a strong chain (`predictor.py`, `smoothing.py`); the catalog makes each a named lever and notes the ones that should be exposed.

### Probability calibration (current: per class isotonic)

- What it does: remaps raw model confidences so a stated 0.75 really means about 75 percent. Hearth fits per class isotonic regression on the held out split after honest evaluation, only when n_val is at least 100.
- Accuracy: does not change the argmax (top prediction unchanged), so headline accuracy is unaffected; it makes the confidence number trustworthy, which every downstream threshold depends on.
- Reliability and overfitting: strongly improves reliability; the risk is fitting the calibrator on too little data (hence the n_val floor). Platt scaling (sigmoid) is the lower variance alternative when data is very scarce (judgment: consider sigmoid below some n, isotonic above).
- Default: isotonic with the n_val floor (keep).
- UI: advanced (on/off plus method), default on.

### Confidence threshold with an abstain or unknown class

- What it does: when top confidence (or margin) is below a threshold, the system declines to assert a class and emits unknown or holds the previous state, and routes the window to a question. Hearth partially does this via the evidence cap (confidence capped to 0.70 when direct evidence share is low, so it asks instead of asserts) and the asking policy.
- Accuracy: trades coverage for precision; on asserted windows precision rises, at the cost of more unknowns. For automations this is usually the right trade (a wrong "movie" dims your lights mid dinner; an unknown does nothing).
- Reliability and overfitting: a pure reliability lever; reduces confident wrong actions, the worst failure mode. Also an imbalance safeguard (Group 4).
- Default: enabled, with an explicit abstain or unknown state exposed to HA (judgment: today the behavior is implicit via capping and asking; making unknown a first class output state is cleaner for automations and honesty).
- UI: basic, framed as "how sure should Hearth be before it commits to an activity?" with a simple slider mapping to the threshold, default moderate.

### Temporal smoothing of the output sequence (current: hysteresis plus learned transitions)

- What it does: damps flicker by requiring consecutive agreement or a decisive margin before switching states (hysteresis, k=2, margin 0.25), and forward filters the probability stream through a learned per household transition matrix mixed 85/15 with uniform (`smoothing.py`).
- Accuracy: improves real world accuracy on the sequence by removing physically implausible single window flips (sleeping to cooking to sleeping); targets the transition errors that dominate (P4). The 15 percent uniform mix ensures a decisive observation can always override the prior, preventing lock in.
- Reliability and overfitting: mostly positive; the risk is over smoothing (sluggish to react to real transitions) if k or the prior weight is too high. The learned matrix can also encode a stale routine if not refreshed (it is relearned each train, which is correct).
- Default: hysteresis k=2, margin 0.25, transition mix 0.15 (keep).
- UI: advanced (smoothing strength as a single low/medium/high control mapping to k and mix), default medium. Basic users should not see k.

### Order of the post processing chain

- The chain today is: transition filter, then hierarchy descent, then evidence cap, then hysteresis (hand sequenced in `predict_person`). This order is a lever in itself (judgment: it is correct as is, but it should be expressed as an explicit ordered, individually toggleable pipeline so it can be reasoned about and so a user can, for example, disable smoothing for debugging). UI: advanced (toggles per stage), the order itself internal.

---

## Recommended first deployment configuration

For a new home's first model, optimize for honesty and stability over peak accuracy. All values are defaults; advanced users can change the advanced ones later.

```yaml
model_family: random_forest        # forgiving, interpretable, no GPU; logistic run as a silent baseline
rf:
  n_estimators: 300
  min_samples_leaf: 5
  max_features: sqrt
  max_depth: null
  class_weight: balanced           # rare activities treated as important
tuning:
  enabled_above_windows: 500       # below this, use defaults (avoid fitting noise)
  cadence_days: 30                 # monthly, not weekly
  cv: time_series_split            # never shuffled
  scoring: f1_macro                # balanced across rare classes

windowing:
  window_minutes: 30               # 15 / 30 / 60 offered; LLM may recommend per home
  stride_inference_minutes: 5      # responsive ribbon and realtime lane
  stride_training_minutes: 30      # equals window length: NO training overlap (anti-leakage)
  resample: 1min
  segmentation: off                # periodic + event-dynamics features (CASAS-aligned)

imbalance:
  class_weights: balanced          # on
  resampling: off                  # SMOTE off (leakage/fabrication risk on time series)
  focal_loss: off                  # not applicable to RF

validation:
  mode: temporal_holdout           # leave-one-day-out offered as rigorous mode
  val_days: 7                      # one full weekly cycle
  headline_metric: accuracy_confirmed_only   # Wilson interval; bootstrap-agreement shown separately, never as headline
  promotion_gate_margin: 0.02      # CI-overlap based
  min_confirmed_before_validated: 30   # (judgment, NEW) below this, model is labeled "provisional, unvalidated" not "ready" — addresses the Step 1 cold-start circularity

postprocess:
  calibration: isotonic_per_class  # fitted post-eval, n_val >= 100; sigmoid fallback below (judgment)
  confidence_threshold: moderate   # abstain/unknown below threshold; routes to a question
  abstain_class: enabled           # unknown exposed to HA as a first-class state (judgment, NEW)
  smoothing: medium                # hysteresis k=2, margin 0.25, transition mix 0.15
```

Two of these are new recommendations rather than current behavior, both labeled judgment, and both trace directly to the Step 1 biggest risk: `min_confirmed_before_validated` (do not tell the user a model is validated when the only signal is circular), and a first class `abstain_class` (let the system say unknown rather than guess). Everything else is the existing, well chosen behavior promoted into an explicit, mostly advanced, lever set.

The basic UI surface (what a non analyst sees) is intentionally tiny: rare activities important (on), how sure before committing (a slider), window length (15/30/60), and smoothing strength (low/medium/high). Everything else is advanced or internal. This keeps the wizard and Settings calm while leaving every lever reachable, which is the Step 5 brief.

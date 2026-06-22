# Hearth — ML Correctness & Reliability Audit

Status: independent ML audit, June 2026. Read-only on the repo. This is **not** a
feature-roadmap (that's `gap_analysis.md`) — it audits whether the ML that is
*already built* is statistically **correct**, where it can be made **more
reliable**, and where **clustering / GMM / HMM** genuinely fit. Findings are
ranked by reliability impact, not effort.

Files read in depth: `training/{trainer,evaluate,estimators}.py`,
`inference/{predictor,smoothing}.py`, `discovery/clustering.py`,
`features/pipeline.py`, plus `README.md` and `gap_analysis.md`.

---

## 0. Verdict

This is mature, careful ML engineering — well above the level most "smart home ML"
projects reach, and frankly above the level of the coursework the methods come from.
The fundamentals that usually go wrong are **right here**:

- **No temporal leakage.** Training uses a real temporal holdout (`train < cutoff`,
  validate on the last `VAL_DAYS`), and hyper-parameter tuning uses `TimeSeriesSplit`
  with an explicit note that shuffled CV leaks adjacent windows. This is the single
  most common ML-in-finance/HAR mistake and it is correctly avoided.
- **The feature pipeline is leakage-clean** (verified line by line): 1-min resample
  + **forward-fill only** with per-role limits, lags via `shift(1)`, every feature
  bounded to `[window_start, window_end)`, idle-time via forward-fill. Nothing reads
  the future. No backfill anywhere.
- **Honest evaluation.** Confirmed-only headline accuracy with a Wilson interval,
  bootstrap-agreement reported *separately* (the circularity is named, not hidden),
  per-class P/R/F1, macro-AUC, confusion matrix, isotonic calibration, a CI-aware
  promotion gate, and a provisional/validated distinction.
- **Sensible inductive choices.** Coarse "part-of-day" time encoding by default to
  stop a tree memorising the clock and crowding out sensors; recency-weighted
  training for drift; `class_weight="balanced"` + `f1_macro` for imbalance.

So the honest answer to *"is what's done now actually right?"* is: **yes, the core
is right.** The issues below are subtle correctness/reliability gaps, not broken
fundamentals.

### What's already built, mapped to the ML concepts

| Concept | Where | Verdict |
|---|---|---|
| Temporal train/val split (no k-fold leakage) | `trainer._fit_node` cutoff; `estimators.tune_hyperparams` TimeSeriesSplit | ✅ correct |
| Feature-engineering leakage avoidance | `features/pipeline.py` (ffill-only, shift(1), window-bounded) | ✅ verified clean |
| Overfitting control | `min_samples_leaf`/`max_depth` tuning, tune floor (≥500 windows), recency weighting, coarse time encoding | ✅ correct |
| Class imbalance | `class_weight="balanced"`, `f1_macro` scoring, `sample_weight` (GBT) | ✅ correct |
| Evaluation metrics | confusion matrix, per-class P/R/F1, macro AUC, Wilson CI, confirmed-only accuracy | ✅ correct |
| Probability calibration | per-class isotonic (`_SklearnEstimator.calibrate`) | ⚠️ correct but see F4 |
| Glass-box importance | SHAP (`_tree_shap`), `feature_importances_`, evidence profile | ✅ correct |
| Model families | RF (variance-reducer), GBT (bias-reducer), logistic baseline, embedding/JEPA stub | ✅ correct |
| Unsupervised clustering | **HDBSCAN**, standardized, constant-features dropped | ✅ good choice, see F2 |
| HMM-style temporal model | `learn_transitions` + `transition_filter` (online forward filter), wired in `predictor` | ✅ operational, see F6 |
| Hierarchical classification (LCPN) | coarse root + child-per-parent | ⚠️ correct but see F3 |
| Drift | PSI function, recency weighting | ⚠️ partial, see F5 |
| Abstain / honesty | evidence-confidence cap, provisional gating, `unknown` publish | ✅ correct |

---

## 1. The three questions, answered directly

**Is clustering / HMM / GMM already done?**

- **Clustering — yes**, and with a *better* algorithm than the obvious one.
  `discovery/clustering.py` uses **HDBSCAN** on z-standardized features (constant
  features dropped). This is a deliberate upgrade over k-means: no `K` to pre-specify,
  no spherical/equal-size assumption, and it isolates noise (`-1`) instead of forcing
  every point into a cluster. For messy, imbalanced home data those are exactly the
  k-means weaknesses you'd want gone.
- **HMM — effectively yes.** `learn_transitions` builds a Laplace-smoothed,
  row-stochastic transition matrix from the household's own coarse-label history;
  `transition_filter` multiplies the classifier's probabilities (the *emissions*) by
  the transition prior (with a uniform escape hatch) and renormalises. That **is the
  online forward-filtering step of an HMM**, and it's wired into `predictor` on the
  root row before hierarchy descent. Correct design for real-time (it only uses the
  past).
- **GMM — no, not used.** And there's a real, specific place for it (§3).

**Is what's done right?** Yes for the fundamentals (§0). Six correctness/reliability
issues remain (§2), none fatal.

**Can it be better?** Yes — §2 fixes + §3 GMM/HMM extensions, ranked in §4.

---

## 2. Findings (ranked by reliability impact)

### F1 — High — The headline metric is measured on a *biased* sample

`accuracy_confirmed` and the **promotion gate** run on `provenance == CONFIRMED`
labels. But confirmed labels come from the inbox, and the inbox asks on
**uncertainty + margin** windows (active learning). So the confirmed set is
dominated by the model's *hardest, most ambiguous* windows — it is not a random
sample of the home's life. Consequences:

- The reported accuracy is a **pessimistic, non-representative** estimate (it's
  conditional on "windows the model was unsure about"), so the glass-box number
  users see understates true performance, and version-to-version comparisons in the
  promotion gate are noisy.
- This is the same idea as "a fair test set must be representative, not the cases you
  cherry-picked."

Mitigation already present: there is `epsilon` exploration asking, so a *fraction* of
confirmed labels are random — good, but they're pooled with the uncertainty-sampled
ones, so the bias remains in the headline.

**Fix:** tag the epsilon-random confirmations and compute the **headline accuracy
+ CI + promotion gate on that unbiased subset only**; keep the uncertainty-sampled
labels for *training* (where hard cases are valuable). Even a small reserved
random-query budget gives a trustworthy metric. This is the highest reliability-
per-effort change in the whole system — it makes every other number honest.

### F2 — High — Discovery clusters in raw high-dim space → curse of dimensionality, rare states lost

`discover_person` runs HDBSCAN directly on the standardized feature matrix (only
constant columns dropped). Two problems compound:

1. **Curse of dimensionality.** With many sensors the feature space is high-dim;
   distances concentrate (everything becomes ~equidistant), which degrades the
   density estimates HDBSCAN relies on → unstable, less meaningful clusters. (This is
   exactly the "don't feed all 1700 variables straight in" concern.)
2. **Rare/subtle activities vanish.** `min_cluster_size = max(8, n/40)` plus
   HDBSCAN's noise label (`-1`, excluded) means a short, infrequent activity
   (cooking, reading) often falls below the floor or is labelled noise → **never
   becomes a pattern card → never gets named → never enters training**. The very
   states you most want are the ones most likely to be dropped.

**Fix:**
- Put **PCA (or UMAP) before HDBSCAN** — standardise → reduce to ~10–30 components
  capturing most variance → cluster there. Denoises, concentrates signal, and makes
  the density estimate meaningful. (Standardise first; the scales are wildly mixed —
  CO2 ppm vs binary presence vs watts — so this is the correlation-matrix logic.)
- Make `min_cluster_size` adaptive/lower with **stability selection** (run a few
  parameterisations, keep clusters that recur).
- Add a **GMM second pass** (see F-GMM in §3) to surface rare states HDBSCAN calls
  noise.

### F3 — Medium — LCPN child models: train/inference distribution mismatch

Child (fine) classifiers are trained on `feats[mask]` where `mask = fine.notna()`
— i.e. windows whose **true** coarse label is the parent. At inference, the child is
applied to windows the **root predicted** as that parent, which includes the root's
errors. So the child sees a different (cleaner) distribution in training than in
production — a covariate shift that makes fine accuracy optimistic and lets root
errors cascade.

**Fix:** train children on **root-predicted-parent** windows (or a mix of true +
predicted parent), and/or report fine accuracy **conditional on a correct parent**
so the metric is interpretable. Also worth keeping a **flat multiclass model** as a
silent baseline to confirm the hierarchy actually beats it (it doesn't always).

### F4 — Medium — Calibration is fit and "trusted" on the same small window, never checked

`calibrate(X_val, y_val)` fits per-class isotonic regression on the **same**
`X_val` used for metrics (metrics are computed *before* calibration, which is
correct), gated only by `len(X_val) >= 100`. Two risks: isotonic on ~100 points
**overfits**, and the calibrators are then deployed with **no calibration metric**
ever reported — "0.75 means ~75%" is asserted, not verified.

**Fix:** fit calibration on its **own slice** (or via cross-fitting), and report a
calibration metric (**Brier score / ECE / a reliability curve**) in `metrics` so the
confidence used by the abstain threshold, asking, and evidence cap is provably
meaningful. (The README already promises this kind of honesty.)

### F5 — Low/Medium — Drift is measured but not acted on

`population_stability_index` exists and is exactly the right tool, but I found no
live monitor that computes it between the training window and recent production
windows, alerts on it, or triggers a retrain — and `gap_analysis.md` F2 confirms the
drift UI isn't built. Given real seasonal/regime drift (your own
summer-vs-winter temperature example — the *same* reading means different things in
different regimes), this is the gap most likely to cause silent degradation.

**Fix:** scheduled per-feature PSI (train vs last N days), alert at >0.2, optionally
auto-trigger a retrain, and surface the trend. Recency weighting helps but is not a
substitute for *detecting* a regime change.

### F6 — Low — Transition model is coarse-only and time-homogeneous

`learn_transitions` is built on **coarse** labels at a fixed 30-min grid and is
**stationary** — the prior for `sleeping → cooking` is the same at 3am and 8am, even
though it's wildly more plausible in the morning. Fine-state transitions aren't
modelled at all.

**Fix (optional):** time-conditioned transition matrices (e.g. one per part-of-day,
which you already compute) and per-parent child transition matrices. Low effort,
removes a class of boundary errors.

*(Checked and clean: no future leakage in feature engineering; the transition filter
is genuinely wired in, not dead code; recency weighting and tuning floors are sound.)*

---

## 3. Where GMM / HMM / clustering genuinely fit

### GMM — yes, add it, in two specific roles

1. **Rare-state discovery (complement to HDBSCAN, attacks F2).** GMM gives **soft,
   probabilistic** assignment and **elliptical** components, so it can model subtle,
   imbalanced states that HDBSCAN buries in noise. Run it *after* PCA, pick the
   number of components by **BIC**, and surface low-population components as candidate
   pattern cards. This is the course-endorsed move for "subtle regimes where class
   imbalance breaks the simpler clusterer."
2. **Novelty / anomaly detection ("something new is happening").** A GMM is
   *generative*, so a window with **low likelihood under every component** is a novel
   routine — or a sensor breaking. That's a clean signal for the "detect → ask" loop
   and for flagging when the home itself has changed (feeds F5).

### HMM — you have the filter; two worthwhile extensions

1. **Offline Viterbi for bulk history relabeling.** Your real-time forward filter
   correctly uses only the past. But when labelling **history** (the Patterns page /
   bulk range labelling) you *can* use the future, so run **full forward–backward /
   Viterbi** there for a globally consistent state sequence — it fixes
   transition-boundary errors better than online filtering. Rule of thumb:
   **forward-filter live, Viterbi-smooth offline.**
2. **A duration-aware model (HSMM).** Activities have dwell times (you sleep for
   hours). A **Hidden Semi-Markov Model** models duration explicitly and reduces
   flicker more principledly than the hand-rule hysteresis — a candidate estimator
   family once data is rich.

### Clustering — keep HDBSCAN, add the PCA front-end (F2) and the GMM second pass.

---

## 4. Reliability roadmap (value-ranked)

1. **Unbiased gold eval set** (F1) — small. Makes every reported number and the
   promotion gate trustworthy. Do this first.
2. **PCA → HDBSCAN + GMM rare-state pass** (F2 + GMM-1) — medium. Unlocks the subtle
   activities the system currently can't discover.
3. **Calibration on its own slice + Brier/ECE reported** (F4) — small. Makes the
   confidence the whole abstain/ask machinery depends on provably real.
4. **Live PSI drift monitor + retrain trigger** (F5 + GMM-2 novelty) — medium.
   Catches regime/seasonal change before it silently degrades predictions.
5. **LCPN children on predicted parents + flat baseline** (F3) — medium. Removes the
   train/inference mismatch and proves the hierarchy earns its complexity.
6. **Viterbi offline relabeling + time-conditioned transitions** (HMM-1, F6) —
   medium. Better history labels and fewer boundary flips.

---

## 5. Bottom line

Hearth's ML core is **correct where it counts** — temporal integrity, leakage-clean
features, honest metrics, calibrated and abstaining output, an HMM forward filter,
and a density clusterer chosen for exactly the right reasons. The reliability ceiling
now is set by **subtler, statistical** issues, not missing machinery: the headline
metric is computed on a biased (uncertainty-sampled) set; discovery clusters in a
space too high-dimensional to find rare states; calibration is unverified; and drift
is measured but unacted-on. Fix the evaluation bias first (cheap, makes everything
else honest), add a PCA→HDBSCAN→GMM discovery stack so subtle activities can surface,
and close the loop with a live drift monitor. GMM earns its place for rare-state
discovery and novelty detection; the HMM you already have earns a Viterbi offline
sibling. None of this is a rebuild — it's tightening a system that is already, by a
clear margin, doing the hard parts right.

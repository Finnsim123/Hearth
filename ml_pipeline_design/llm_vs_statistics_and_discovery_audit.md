# Hearth — LLM Feature Engineering vs Statistics, and the Discovery Conflict

Status: critical assessment, June 2026. Read-only on the repo. Answers four
questions: **(1)** what the LLM actually does in feature engineering and whether
it's the right tool; **(2)** where *statistics* would do the job better; **(3)**
whether the LLM layer **stands in the way of clustering**; **(4)** how clustering
+ asking are presented, and the path to a system that knows when it's blind and
says *"add a sensor in the kitchen."*

Code read: `onboarding/feature_architect.py`, `features/{spec_builder,validate,
transforms,pipeline}.py`, `discovery/clustering.py` (current), `labeling/active.py`,
`labeling/phrasing.py`, `discovery/{evidence,lexicon}.py`, `methodology.py`, the
Patterns/Inbox/Sensors frontend. **Note:** the discovery pipeline now runs
PCA → HDBSCAN → **GMM-rescue** (audit F2 already shipped) — this assessment reflects
the *current* code, not the earlier version.

---

## 1. What the LLM actually does — and it's well-bounded

The architect runs **once** (setup / re-analysis), in three passes, then a revision
pass in the feedback loop:

1. **Selection** — per entity: `keep`, `role`, **information tier T0–T5**, owning
   person, **reliability** (ok/suspect/unusable), one-line reason.
2. **Per-entity features** — for each kept entity, ≤6 features chosen from a
   **transform whitelist** (it picks `transform id + params + window`, never writes
   code), tier-compatibility enforced (e.g. a T4 counter must use a rate/delta).
3. **Composites** — cross-entity features over *existing feature names*
   ("sofa + TV + low light = movie").
4. **Revision** (feedback loop) — given the confusion matrix + discriminative stats,
   ADD ≤4 features for the most-confused pairs, DROP zero-importance ones.

The **safety envelope is genuinely good** and worth stating before any criticism:

- **Never writes code** — selects/parameterizes a vetted whitelist only.
- **Never sees raw time series** — only metadata + aggregate stats (privacy +
  grounding); reasons about semantics.
- **Never in the prediction loop** — one-time design; no inference-time cost,
  latency, or nondeterminism. Predictions are 100% local sklearn.
- **Output validated item-by-item** — malformed selections/features are dropped, not
  trusted.
- **Hybridised with statistics already**: `audit_reliability` takes the **more
  severe** of the LLM's reliability call and a deterministic stats verdict
  (`heuristic_reliability`), so observed stats can override the model; there's a
  no-LLM heuristic floor; the revision pass is driven by importance + Cohen's d.
- The system prompt even contains the right instinct: *"the model already learns
  thresholds and interactions, so do not propose redundant threshold features; spend
  effort on signal typing, composites, and flagging unreliable sensors."*

So this is not a naive "ask GPT to do ML" design. The question is sharper: **for
each of the four jobs, is the LLM the best tool, or would statistics be?**

---

## 2. LLM vs statistics — job by job

The decisive variable is **labels**. The LLM's only real edge is *semantic priors
without labels*; that edge **decays to zero as confirmed labels accumulate**, after
which measurement beats guessing. Grading each job on that axis:

| Job | LLM at cold start | Once labels exist | Verdict |
|---|---|---|---|
| **Sensor selection (keep/drop)** | **Strong** — reads names/`device_class` and *knows* a lamp-heartbeat counter is junk and bed-occupancy is gold, with zero labels. Pure variance/correlation can't: a flapping junk sensor looks "informative", a mostly-zero bed sensor looks "boring". | **Statistics win** — RF importance, permutation, mutual-information with the label measure *actual* value. | LLM-prior at cold start → **hand authority to stats as labels arrive.** |
| **Information tiering (T0–T5)** | Mostly **deterministic** from `device_class`/`state_class` (a `total_increasing` *is* T4; a `binary_sensor` *is* T1). LLM only needed where metadata is missing/wrong. | Same. | **Deterministic-first, LLM for the gaps** — don't spend the LLM (or tokens) tiering entities whose metadata already answers it. |
| **Per-entity feature design** | **Weak.** Trees are scale-invariant and *learn thresholds and interactions themselves*; "compute the whole valid transform bank, prune by importance/regularisation" is more reliable than an LLM's guess, and window length is an empirical (CV) question, not a guess. | **Statistics win clearly.** | The LLM's value here is **compute economy + interpretability**, *not* predictive power. Demote it: generate the bank, prune statistically. |
| **Cross-sensor composites** | **Moderate–strong.** A pre-made "sofa+TV+light=movie" buys **sample efficiency** — a tree needs many labels to learn a 3-way interaction; a composite needs few. | Decays — given enough data the tree learns the interaction anyway. | Keep for cold start + interpretability; let trees subsume it later. |

### The single most important reframing
**The LLM should be explicitly a cold-start + translation device, and statistics
should be the standing authority.** Today the *initial* spec is LLM-authored and only
the *revision* loop is statistical. Flip the emphasis: as confirmed labels cross a
threshold, **re-derive the kept-set and feature value from data** (importance +
permutation + mutual information + CV-tuned windows) and let the LLM *propose
candidates that statistics then accept or reject* — never the reverse. The LLM's
durable jobs are the two things statistics genuinely can't do: **semantic typing
with no labels**, and **turning numbers into human-readable features, rationales and
advice.** Everything measurable should be measured.

### Concrete changes
1. **Cheap deterministic pre-filter before the LLM ever runs.** Variance / flatline /
   mostly-missing / metadata-tiering are pure stats (you already have
   `heuristic_reliability`). Run them first; send the LLM only the *ambiguous*
   entities (no `device_class`, odd names). Fewer tokens (helps the governor),
   more reliable, LLM focused where it's actually better.
2. **In "full" feature mode, don't have the LLM pick per-entity transforms at all** —
   generate the full valid bank and let RF importance + (optional) Lasso prune.
   Reserve the LLM for composites + naming. (Conservative mode already ≈ the fixed
   recipe bank — make that the statistical default, not a fallback.)
3. **Tune `window_min`, don't ask for it.** A tiny per-feature CV/grid beats an LLM
   number; activities live on different timescales and that's measurable.
4. **Make the hand-off explicit and visible.** A per-spec flag: "selection authority:
   LLM-prior (cold start)" → "data (N confirmed labels)". Ties to the
   provisional/validated honesty you already have.

---

## 3. Does the LLM stand in the way of clustering? **Yes — and this is the deepest finding.**

Discovery reads the **same spec-built feature matrix as the model**
(`discover_person` → `read_features(person_id, fset, …)`, where `fset` is the active
feature-set built from the active spec's selections). So:

> **Any sensor the LLM dropped as "not useful for the *target* activities" is also
> invisible to the *unsupervised* discovery that is supposed to find activities you
> haven't defined yet.**

This is a real conflict between supervised feature *selection* and unsupervised
*discovery*. Example: the LLM sees the seed taxonomy (sleeping/cooking/movie/away),
decides the **garage door** and **workshop power** sensors don't serve those, sets
`keep=false`. Now clustering can **never** surface "tinkering in the garage" as a
pattern card — the signal that defines it was pruned upstream. Feature selection
optimised for the *known* taxonomy systematically blinds discovery to the *unknown*.
PCA/standardisation in discovery can re-weight what's there but **cannot recover a
dropped column.** The composites compound it: they bake the known taxonomy into the
space, nudging clusters to collapse onto activities you already named.

**Fix — decouple the two feature spaces by separating two different filters:**

- **Junk filter (statistics, strict):** drop only *truly* dead/constant/mostly-missing
  sensors. This is the floor for *both* paths.
- **Relevance filter (LLM/importance, for the supervised model only):** keep the
  subset that predicts the *current* taxonomy.

Then: **the predictor trains on the relevance-filtered subset; discovery clusters on
the junk-filtered *superset*.** Discovery must see *more* of the home than the
predictor, precisely so it can propose activities the taxonomy doesn't cover yet —
which is the whole point of "truly understand the home." This is a small change (a
second, broader feature view for discovery) with a large payoff for your stated goal.

---

## 4. Clustering + asking UX — strong, with specific gaps

The presentation is genuinely good and shows real care:

- **Pattern cards practice recognition over recall.** Each card leads with an
  *evidence story* — a plain summary ("mostly weekday evenings, ~21:00–23:00, living
  room; defined by: media playing"), **example moments** ("a few times this happened
  — what were you up to? Tue 2 Jun 15:10…"), adjacency ("usually after dinner, leads
  into sleep"), and a contrast hint ("looks a lot like *movie* — maybe the same
  thing"). Naming weeks of history in one tap. This is the right UX: people name
  *"what was I doing then"* far more easily than a statistical blob.
- **LLM naming is metadata-only and optional** — suggestions surface first as "was
  it…" pills (with rationale + confidence on hover), with a clean manual fallback and
  honest empty states ("the assistant couldn't pin this one down").
- **Asking is disciplined and self-aware.** Margin sampling (cooking 55% vs eating
  43% is asked even though 0.55 isn't "low"), epsilon exploration (so confidently-
  wrong predictions get corrected — and these are your unbiased gold labels, which is
  exactly the fix for the eval-bias finding in the ML audit), quiet hours, daily
  budget, cooldown, same-label suppression, silent-activity handling, and channel
  routing (push vs inbox). Crucially, the **evidence cap** forces predictions that
  lean on weak/ambient signal *below* the ask threshold — so a model that's "confident
  for the wrong reasons" is structurally made to ask. That's excellent.
- **Phrasing adapts to the uncertainty shape** (confident / toss-up / unsure) with an
  escape chain that can always reach the right answer.

Gaps worth closing:

1. **The discovery algorithm is invisible.** PCA→HDBSCAN→GMM runs, `algo` is stored
   per card ("hdbscan"/"gmm"), but the UI never shows it and the spec'd UMAP scatter
   isn't built. Surfacing "found via GMM (a subtle/rare pattern)" would build trust
   and explain *why* a faint pattern is worth naming.
2. **A threshold mismatch.** `active.py` asks when the top-2 margin < **0.25**, but
   `phrasing.py` only phrases as a "toss-up" when the gap < **0.15**. So a window in
   the 0.15–0.25 band is *asked as if confident* ("Are you cooking?") when the model
   is actually torn between cooking and eating. Align them (or make phrasing read the
   same margin) so the wording matches the real uncertainty.
3. **Inbox is thinner than the spec** — no per-question sensor summary, no
   split-window, and it shows the raw `person_id` instead of the friendly name.
4. **No "add a sensor" advice anywhere** — see §5.

---

## 5. The real goal: a home-model that knows it's blind and says so

Today self-awareness stops at *existing* sensors: suspect/dead/constant flags,
missing-`away` coverage, `weakest_room`, "ghost" rooms with no usable sensor. The
system never says *"add a sensor in the kitchen."* But — and this is the good news —
**every input needed to generate that advice already exists**; it just isn't joined
up. The advice should be **detected by statistics and phrased by the LLM.**

**The detector (pure statistics, no LLM):** for each confused activity pair (read
straight from the confusion matrix), join three things you already compute:

- the **discriminative stats** (Cohen's d per feature) — which signal *would* separate
  the pair, and whether you currently have it;
- the **room/role coverage** — which room the pair lives in (from the signature's
  `where`) and what sensor *roles* exist there;
- the **evidence tier reliance** — is the model leaning on ambient (weak) signal for
  this activity (the capped-confidence case)?

That yields a precise gap statement: *"cooking vs eating are confused, both in the
kitchen, separated only by signals you don't have, and the model is leaning on
ambient evidence there."* Combine with **ghost rooms / `weakest_room`** for the
coarse version ("you have no sensor that sees the kitchen at all").

**The phrasing (LLM, its genuine strength):** turn that structured gap into one
warm, concrete sentence — *"I keep mixing up cooking and just eating. A smart plug on
the hob or a kitchen motion sensor would let me tell them apart."* The LLM is ideal
here precisely because this is *translation*, not measurement.

**Surface it** on the Sensors page ("Where I'm blind") and through the **buddy**
(*"is it worth me adding anything?"* → reads the gap detector, recommends the highest-
value sensor, estimates the confusion it would resolve). Gate it on data sufficiency
you already track (provisional/validated, min-windows, untrainable-class warnings) so
it only advises when it has earned the right to. This is the concrete path from
"recognises activities" to **"understands the home, knows what it can't see, and asks
for the one sensor that would help most."**

---

## 6. Bottom line

The LLM layer is **well-engineered and correctly bounded** — out of the inference
loop, code-free, privacy-preserving, validated, already hybridised with deterministic
stats. The corrections are about **emphasis, not safety**:

1. **Statistics should be the standing authority; the LLM a cold-start prior +
   translator.** Its edge is semantic selection/typing *without labels* and turning
   numbers into human-readable features and advice — everything measurable (per-entity
   transforms, windows, kept-set value) should be *measured* (importance, permutation,
   mutual information, CV), especially as confirmed labels accumulate.
2. **Tier/junk-detection deterministic-first; LLM only for genuine ambiguity** —
   cheaper, more reliable, fewer tokens.
3. **Decouple discovery from the model's feature space** — the deepest fix. Cluster on
   a junk-filtered *superset*, train on the relevance-filtered subset, so the LLM's
   supervised pruning never blinds unsupervised discovery to activities you haven't
   named. This is essential to "truly understand the home."
4. **The UX is strong** — keep recognition-over-recall, fix the 0.15/0.25 margin
   mismatch, surface the discovery algorithm, and thicken the inbox.
5. **Build the blind-spot advisor** — statistics detect the gap (confused pairs ×
   room coverage × evidence reliance × ghost rooms), the LLM phrases the
   recommendation, the buddy delivers it. That's the bridge to a system that says
   *"add a sensor in the kitchen — I can't see clearly there."*

The unifying principle: **let the data decide, and let the LLM explain and advise.**
Use statistics for every choice you can measure, reserve the LLM for the two things it
alone does well — semantic priors with no labels, and human language — and never let
the model's supervised view of the home narrow the unsupervised view that is meant to
expand it.

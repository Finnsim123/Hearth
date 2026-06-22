# Hearth — Foundational Facts: a precedence cascade over the model

Status: brainstorm / design direction, June 2026. The thesis: **Hearth's output is the
per-person activity classification; the *path* to each output should be the cheapest
sufficient one.** Some of that output is already ground truth in Home Assistant
(home/away from a `person` entity). Predicting what HA already knows wastes compute and
can only be *less* reliable than the fact. So: known facts **bypass** the model, the
model does only the residual, and everything still funnels through one Hearth output
entity so no extra logic lives in HA.

This is the well-trodden **neuro-symbolic / hybrid** pattern: symbolic rules for
deterministic + contextual states, ML for the ambiguous ones (see Sources).

---

## 1. Reframe: Hearth = sensor-fusion + *residual* ML

Today the model is asked to classify everything, including states HA hands us for free.
Reframe Hearth as: **HA provides facts; Hearth's job is the residual** — the part the
facts can't determine. Two payoffs beyond compute:

- **Honest metrics.** Including "away"/"asleep" in the model's accuracy inflates it with
  trivially-correct windows. Train + evaluate the model only on the *residual* (home &
  awake) and its reported accuracy reflects the hard work it actually does. (This
  compounds the earlier eval-bias fix.)
- **Cold start.** Facts give perfect labels for away/asleep from day one, and seed
  high-trust training labels for those classes — the model never has to learn them.

## 2. The certainty ladder

Classify every output state by how knowable it is, and route to the cheapest layer that
settles it:

| Tier | Examples | Source | Treatment |
|---|---|---|---|
| **Ground truth** | away / not-home, house empty, manual override, asleep *if a sleep/bed sensor exists* | `person`/`device_tracker`/zone; a UI/HA control; a sleep sensor | **bypass the model** — emit the fact |
| **Near-certain primitive** | "someone is in the kitchen" | wasp-in-a-box / Bayesian occupancy (HA community patterns) | **constrain** the model (mask impossible classes) + feature |
| **Strong rule (fallible)** | shower running → showering; hob on → cooking *likely* | appliance/water/humidity sensors | rule **prior** / cold-start fallback; ML may override |
| **Genuinely ML** | cooking vs eating vs reading vs working vs movie | the learned model | **the model** (with transition filter) |

## 3. Two flavours of fact — the key distinction

The "away" insight generalises into two kinds:

- **Gating facts** are *mutually exclusive with the whole in-home taxonomy*: **away,
  house-empty, asleep**. When true they **are** the answer → short-circuit: skip feature
  build + inference entirely (correctness *and* a big compute saving — this is where the
  governor and this design meet).
- **Constraining facts** narrow but don't decide: **room occupancy** ("in the kitchen"
  rules out bedroom activities), which person is where. These become **hard masks** on
  the classifier's output (zero the impossible classes) *and* features — they prune the
  option space, they don't replace the model.

So: ground-truth gating facts bypass; constraining facts mask + feature; strong rules are
priors/fallbacks. Only true ground truth bypasses.

## 4. Bypass *and* feature — not either/or

For a known fact you can (a) feed it to the model or (b) bypass the model. The right
answer for ground truth is **both**:
- **Bypass** as a gate/override (compute + guaranteed correctness).
- **Feature** the same fact for the *residual* classification — "home for 5 minutes"
  helps predict "just got back, making dinner." A fact that gates the top level can still
  inform the level below.

## 5. The precedence cascade (one resolver → one output entity)

Resolution order in the inference pipeline, highest wins, each short-circuits the rest:

1. **Manual override** — the user said "movie now" (ground truth for its window). *(exists: `active_override`)*
2. **Gating facts** — away / empty / asleep(sensor). Skip the model. *(partly exists: person-link + "can't predict away")*
3. **ML classification** — home & awake, masked by constraining facts, blended with the
   learned transition prior. *(exists: predictor + transition filter + hierarchy)*
4. **Strong rules** — deterministic fallback when ML abstains (cold start). *(exists: rules fallback)*
5. **`unknown`** — abstain when nothing is confident. *(exists)*

The published `sensor.hearth_<person>_activity` carries an extra attribute
**`basis: override | fact | model | rule`** and confidence (1.0 for facts) so automations
and the UI know whether a state is *known* or *inferred*. HA consumes one entity; no
extra logic.

Most of this is **reorganisation, not new ML** — Hearth already has override, person
links, the rules engine, the LCPN hierarchy, the transition filter and abstain. The new
piece is a `foundational` resolver that runs *before* the model and a small registry.

## 6. The foundational-rules registry (curated, opt-in, broadly applicable)

A built-in set of foundational rules — each with: the fact it asserts, the entity role it
needs, its kind (gate/constrain/strong), precedence, a privacy note, and on/off.

| Rule | Asserts | Needs | Kind |
|---|---|---|---|
| **Away / not-home** | person is out → `away` | `person`/`device_tracker`/zone | gate (per person) |
| **House empty** | all away → `empty` | all persons' presence | gate (household) |
| **Asleep** | `asleep` | sleep tracker / bed sensor, or "in bed + night + dark + still" | gate (per person) |
| **Manual override** | the chosen activity | an `input_select`/UI/HA control | override |
| **Room occupancy** | "in room X" | wasp-in-a-box / Bayesian occupancy | constrain |
| **Appliance-locked** | showering / cooking-likely | water-flow / humidity / hob power | strong rule |
| **Guest / DND / vacation** | declared state | a user toggle | override/gate |
| **At work** | refines `away` | calendar + away | gate refinement |

## 7. Reliability — facts are *mostly* true (from the research)

HA presence is the best home/away source but imperfect: GPS drift, phones dropping Wi-Fi
when asleep, app lag — which is why HA has `consider_home` debounce and why the docs
recommend the `person` entity (it fuses app + router + BLE and picks the most recent).
Design implications:
- Prefer binding the **`person`** entity (fused) over a single tracker; let the user pick.
- **Debounce** the gate (a `consider_home`-style hold) so the output doesn't flicker on a
  momentary GPS blip.
- High-confidence override, **not infallible**: if the model strongly indicates an in-home
  activity while presence says away, that's a *conflict signal* — surface it (sensor
  problem or stale tracker), optionally ask. Feeds the drift/coverage work.
- Corrections still flow through the feedback loop — a fact can be overruled by a human.

## 7a. The reliability gate — a fact must EARN bypass

A foundational fact is only as trustworthy as its sensor. `person` home/away is
inherently reliable (binary, slow-changing, corroborated by everything); a bed/sleep
sensor is notoriously noisy. So **bypass status is earned, not assumed** — and the
critical design move: a sensor that *fails* the gate isn't thrown away, it's **demoted
to a feature/prior** the model weighs contextually. An unreliable bed sensor is a bad
*fact* but can still be a useful *hint*.

Three tiers of evidence, cheapest first (no manual labels needed for the first two):

1. **Physical-plausibility self-checks.** Does the signal behave like the thing it
   claims? Role-specific, computed from history (extends the existing `heuristic_
   reliability` stats):
   - *sleep/bed*: a contiguous "in bed" block of ~4–10 h overlapping night on most
     nights; low intra-night flip rate (a sensor toggling 38×/night is noise); not
     stuck, not mostly-missing.
   - *presence*: a handful of transitions/day, plausible segment lengths, not frozen
     "home" forever (dead tracker), not mostly-missing.
2. **Cross-signal corroboration.** Does the candidate fact agree with the rest of the
   home? When the bed says "asleep" it should usually be night + lights off + low
   whole-home motion + no media. "Asleep" at 3pm with the TV on and kitchen motion is a
   **contradiction**; the *contradiction rate* across history is a label-free reliability
   score. (Presence: "away" while indoor motion fires → contradiction.)
3. **Agreement with confirmed labels** (when they exist). Precision/recall of the
   sensor's assertion vs human-confirmed truth — the gold standard, available once the
   feedback loop has run.

These roll into a **reliability score (0–1)** that decides the sensor's role:

| Score | Role | Behaviour |
|---|---|---|
| high | **fact** | bypass the model (gate/override) |
| medium | **feature/prior** | fed to the model, never bypasses — the model learns when to trust it |
| low / broken | **suspect/unusable** | flagged (existing path), maybe excluded |

**Default eligibility** encodes the asymmetry you pointed out: `person` presence and
manual override are **fact-eligible out of the box** (they clear a low bar trivially);
**everything else must earn it** over a watched period. So the wizard offers presence as
a fact immediately, but for a bed sensor says: *"I'll watch this for a week and tell you
if it's reliable enough to trust for sleep — until then I'll use it as a hint, not a
fact."*

**Continuous, not one-shot.** A sensor degrades (battery dies, gets moved). Re-score on
the drift cadence; **auto-demote fact→feature** when plausibility/corroboration drops and
notify: *"your bed sensor has gotten flaky (now flipping 40×/night) — I've stopped
trusting it for sleep and gone back to inferring it."* This reuses the drift/health
machinery.

Even a "100% factual" sensor still clears a *basic* sanity check (exists, updates, not
frozen) — presence just passes it easily; the bar simply scales with how much certainty
the fact claims.

## 8. UX / wizard — your idea, generalised

A wizard step **"What I can know for sure"** (after sensor inventory): a list of
foundational rules, each a **toggle + entity binding + plain explanation + privacy note**:
- *"Use your location for home/away? When you're out I won't guess — I'll know, and I'll
  skip the model (saves energy)."* → connect `person.alice`. (Default on when a `person`
  entity exists; clearly disclosed.)
- *"Have a sleep or bed sensor? I'll mark you asleep directly instead of inferring it."* →
  connect entity, then it must **pass the reliability gate (§7a)** before it's trusted as
  a fact: *"I'll watch it for a week — if it's reliable I'll treat sleep as known;
  otherwise I'll use it as a hint."* The page shows its live reliability score + reason.
- *"Publish a manual override you can set from HA?"* → on.

Principles: **privacy-first, opt-in per fact** (some users won't expose location — then it
can be *bypass-only*, never a model feature); each toggle says exactly what it does
(bypass + override) and how reliable it is. Mirror it on a **Settings → Foundational
rules** page with a live *"away covers 38% of your time — those windows never touch the
model"* stat, and explain it on the **Methodology** page (*"when you're away or asleep, I
know — I don't guess"*). On the **Sensors** page, mark entities bound as foundational
distinctly from model features.

## 9. Subtleties to get right
- **Mutual exclusivity:** away/asleep/empty are top-level states that *replace* the
  activity set; cooking/eating co-occur and stay in the parent/child hierarchy.
- **Multi-person:** presence is per person; "empty" is the AND of all-away (a
  household-level global skip). Room occupancy is shared.
- **Don't pollute discovery:** fact-covered windows (away/asleep) are *explained* — exclude
  them from clustering, like confirmed windows already are.
- **Conflict = signal:** model-vs-fact disagreement is diagnostic (stale tracker, broken
  sensor, or model error) — log/surface it.
- **Governor tie-in:** "don't compute what you already know" — gating facts let the
  scheduler skip feature build + inference for away/empty/asleep persons. Pure efficiency.

## 10. Bottom line
Make Hearth a **fact-first cascade**: manual override → ground-truth gates (away / empty /
asleep) → masked ML on the residual → rule fallback → unknown, all funnelled into the one
activity entity with a `basis` attribute. Known facts bypass the model (cheaper, and a
fact beats a prediction); the model is trained and judged only on the residual it
genuinely has to infer; constraining facts prune its choices; and a privacy-first wizard
lets each household opt each fact in and bind the entity. It's mostly a re-org of pieces
Hearth already has, plus a small foundational-rules registry — and it makes the system
both leaner and more trustworthy: *it only guesses where it has to, and it tells you when
it's guessing.*

---

## Implemented (this pass) + how to wire it into the predictor

Built, pure + tested (33 new tests across the foundational + system modules):
- `domain/foundational/reliability.py` — the §7a gate: `score_foundational(fact,
  profile, contradiction?, truth?)` → `ReliabilityVerdict{role_decision: fact|feature|
  suspect, score, eligible, checks, reason}`. Profiles `PRESENCE` (fact-eligible
  default) and `SLEEP` (must earn it). (`tests/test_reliability_gate.py`)
- `domain/foundational/resolver.py` — the §5 cascade: `needs_model(ctx)` +
  `resolve(ctx) → Resolution{predicted, confidence, basis, model_used}`.
  (`tests/test_foundational_resolver.py`)

Wiring into `inference/predictor.py` (the cascade replaces the ad-hoc override
handling, keeps the single output entity):

```text
for each window:
    ctx = ResolveContext(
        override = active_override(repo, person)            # exists today
        gates    = [Gate(slug, conf) for each foundational sensor whose
                    reliability verdict == 'fact' AND is asserted now]   # away/asleep/empty
        rule     = RuleHint(...) from bootstrap_labels when cold-start    # exists today
        abstain_threshold = load_output_policy(repo).threshold           # exists today
        blocked  = constraining-fact mask (room occupancy)  # later
    )
    if needs_model(ctx):
        probs = est.predict_proba(window)        # only NOW — skipped for facts
        probs = transition_filter(probs, prev)   # exists today (HMM forward filter)
        ctx.model_probs = probs.to_dict()
    res = resolve(ctx)
    publish(predicted=res.predicted, confidence=res.confidence,
            attributes={"basis": res.basis})     # NEW: basis attribute
```

### Wired and working (this pass)

`inference/predictor.py` now runs the cascade for the **universal fact, away** — the
one every HA home has:
- `presence_state` (existing `{name}_home_last` feature, was unwired) decides the gate;
- **away windows bypass the model**: the model/SHAP is computed only on non-away
  windows (`model_todo`) — real compute saving, and `store.load` is never called when a
  person is out;
- the prediction is emitted with `model_version="fact-v0"` (basis carried in the
  existing field — no schema change), `confidence=1.0`;
- **home windows** still run the full model path but with `away` zeroed (`gate_row`) —
  a present person can't be classified away;
- **precedence holds**: manual override still wins over a fact.

Tested end-to-end (`tests/test_predictor_away_fact.py`): away→fact+model-skipped,
home→model/rules+never-away, mixed per-window. 36 new tests pass across the
foundational + system modules.

### Earned facts (sleep, …) — now fully wired too

The whole loop works end-to-end, not just away:
- `domain/foundational/facts.py` — `FoundationalFact` config (in settings, no
  migration), signal extraction (presence `_home_last`, bed `_occupied`,
  awake-evidence contradiction), `run_verdicts()` scoring each bound fact via the
  reliability gate, and `extra_gate_slugs()` (facts whose verdict == `fact`).
  (`tests/test_foundational_facts.py`)
- `scheduler.py` — `foundational_verdicts` job, daily, alongside drift (auto re-scores
  → auto-demotes fact→feature on degradation).
- `api/foundational_routes.py` — `GET /api/foundational` (facts + verdicts +
  bindable candidates), `POST` to bind, `POST /{id}/toggle`, `DELETE /{id}`,
  `POST /run` (the wizard's "test it" button). Wired in `main.py`.
- `inference/predictor.py` — multi-gate: away (presence) + any earned non-away fact
  (e.g. asleep) bypass the model; the rest run the full model path.
  (`tests/test_predictor_away_fact.py::test_earned_sleep_fact_bypasses_model_when_home`)
- `frontend/src/components/FoundationalFacts.tsx` — "What I can know for sure": away
  status, bind a sleep/bed sensor, live verdict pill (fact/feature/suspect + score +
  reason), enable/disable/remove, "test reliability now". Works in Settings and the
  wizard (`<FoundationalFacts wizard />`).

**43 backend tests pass.** Two-line insertions left (additive, low-risk): render
`<FoundationalFacts />` in `Settings.tsx` and add a wizard step using
`<FoundationalFacts wizard />`. Still open (smaller): the constraining-fact mask (room
occupancy → `blocked`), excluding fact-covered windows from discovery, and a Sensors-
page reliability column. Verify with `cd backend && pytest` and `cd frontend && npm run
typecheck` before committing.

## Sources
- [Setting up presence detection — Home Assistant](https://www.home-assistant.io/getting-started/presence-detection/)
- [Person integration — Home Assistant](https://www.home-assistant.io/integrations/person/)
- [Device tracker — Home Assistant](https://www.home-assistant.io/integrations/device_tracker/)
- [Better presence detection in Home Assistant — Home Automation Guy](https://www.homeautomationguy.io/blog/home-assistant-tips/better-presence-detection-in-home-assistant)
- [Area Occupancy Detection / "Wasp in a Box" (HA community)](https://hankanman.github.io/Area-Occupancy-Detection/features/wasp-in-box/)
- [HA Wasp-In-A-Box helper (GitHub)](https://github.com/andrew-codechimp/HA-Wasp-In-A-Box)
- [Neuro-Symbolic Approaches for Context-Aware Human Activity Recognition (arXiv)](https://arxiv.org/pdf/2306.05058)
- [Activity Recognition Using Hybrid Generative/Discriminative Models (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3690009/)

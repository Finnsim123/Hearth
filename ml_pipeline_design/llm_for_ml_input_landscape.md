# LLM for ML Input: Landscape and Mapping to Hearth (Step 2)

Status: research brief, June 2026. Read only on the repo.
Purpose: survey the current state of using LLMs to optimize what goes into an ML model, then map each finding to Hearth's setting (thousands of heterogeneous Home Assistant entities, per person Random Forest, label scarcity, strict "spend once at design time, run free at inference" cost model). Every method gets a "what transfers" and "what does not" verdict. The brief ends with an explicit recommendation on where the LLM should sit.

A note on framing, because it determines everything below. There are two completely different ways to use an LLM near an ML pipeline:

1. Design time feature architect: the LLM reads dataset semantics once, emits feature definitions or rules as code or structured specs, and then leaves. A cheap downstream model trains and serves. Cost is paid once.
2. Inference time recognizer: the LLM reads the raw or summarized signal every window and emits the prediction itself. Cost is paid per prediction, forever.

Your stated cost model rules out pattern 2 for steady state. The tabular feature engineering literature is almost entirely pattern 1. Most of the smart home HAR LLM literature is pattern 2. That tension is the spine of this brief.

## Part A: LLM feature engineering for tabular data (pattern 1)

This is the literature most directly aligned with Hearth, because once Hearth's window builder produces a feature row it is a tabular learning problem (one row per 30 minute window, columns are features, target is the activity).

### CAAFE (Hollmann, Müller, Hutter, NeurIPS 2023)

What it is: Context Aware Automated Feature Engineering. An LLM is given a dataset description and iteratively proposes new features as executable Python, plus a natural language justification for each. Each proposed feature is kept only if it improves validation performance; the code is executed in a controlled environment. Reported lift was mean ROC AUC 0.798 to 0.822 across 14 datasets, methodologically simple ([arxiv 2305.03403](https://arxiv.org/abs/2305.03403), [code](https://github.com/noahho/CAAFE)).

What transfers to Hearth: almost the entire pattern. CAAFE is the canonical reference for the exact thing you chose in our Step 1 questions (executable feature spec). The two ideas you should lift directly are (a) the LLM emits a transform plus a rationale, and (b) every transform is accept or reject gated on a downstream validation score, never trusted blind. Hearth already has the safe execution half solved for rules (the `validate_predicate` AST whitelist in `openrouter_llm.py`); CAAFE is the proof that the same pattern works for feature construction, not just labeling.

What does not transfer: CAAFE writes and executes arbitrary Python (`df['new'] = df['a'] / df['b']`). Hearth must not execute LLM authored Python (it is a local first product touching a user's home; arbitrary code execution is an unacceptable surface). So you take CAAFE's accept/reject loop and rationale discipline but replace its "LLM writes Python" mechanism with "LLM selects from a parameterized, whitelisted transform grammar that a deterministic builder executes" (designed in Step 3). Also, CAAFE assumes a single static table; Hearth's data is temporal, so CAAFE's feature ideas (ratios, products of columns) are necessary but not sufficient (they ignore windowing and temporal leakage, covered in Part C).

### FeatLLM (Han, Yoon, Arik, Pfister, ICML 2024)

What it is: the LLM acts as a feature engineer for few shot tabular learning. It extracts per class rules from a handful of labeled examples plus prior knowledge, parses them into binary features, and a trivial downstream model (e.g. linear regression) consumes them. Crucially it does not query the LLM per sample at inference; it needs only API level access and sidesteps prompt size limits by working from rules. Reported about 10 percent average gain over TabLLM and STUNT ([PMLR v235 han24f](https://proceedings.mlr.press/v235/han24f.html), [arxiv 2404.09491](https://arxiv.org/pdf/2404.09491)).

What transfers: two things matter for Hearth. First, FeatLLM is explicitly designed around few shot, which is Hearth's permanent condition (label scarcity, RESEARCH.md P1). Second, it makes the architectural commitment you want: the LLM produces rules once, the downstream model is cheap, and the LLM is not in the inference loop. This is independent validation that pattern 1 works in the low label regime, not just on big benchmark tables.

What does not transfer: FeatLLM's downstream model is deliberately weak (linear). Hearth's downstream model is a Random Forest, which already learns interactions, so Hearth needs fewer LLM authored binary rules than FeatLLM does; the LLM's marginal value in Hearth is concentrated in semantic features the RF cannot invent from raw columns (cross sensor composites with domain meaning), not in basic thresholding the RF does for free. Judgment: do not over generate binary rule features the way FeatLLM does; the RF makes most of them redundant.

### OCTree (Nam et al., NeurIPS 2024)

What it is: Optimizing Column feature generator with decision Tree reasoning. The LLM proposes feature generation rules without a predefined search space; a decision tree trained on the current features is serialized into natural language and fed back to the LLM as reasoning feedback, so the LLM iteratively improves features using the lessons of prior rounds ([arxiv 2406.08527](https://arxiv.org/abs/2406.08527), [code](https://github.com/jaehyun513/OCTree)).

What transfers: the feedback representation is the key idea for your Step 3 feedback loop. OCTree's insight is that a trained tree is itself an explanation the LLM can read ("the model splits on bed_occupied at 0.5, then on hour bucket"). Hearth already extracts exactly this kind of artifact: `feature_importances_`, top 15 importances, per class confusion, SHAP, and an evidence profile (all in `trainer.py` metrics). OCTree says: serialize those into the next LLM prompt so the maintenance pass reasons from what the current model actually learned, not from priors alone. This is the literature backing for the maintenance pass you asked for.

What does not transfer: OCTree iterates many LLM rounds per dataset to chase benchmark accuracy. Hearth's cost model wants few rounds (you do not want token burn). So adopt OCTree's feedback content but cap the iteration count hard (a stopping criterion, designed in Step 3 section f), rather than running to convergence.

### LLM-FE (Abhyankar, Shojaee, Reddy, 2025)

What it is: treats the LLM as an evolutionary optimizer for feature engineering. It maintains a population of candidate feature programs, uses structured prompts to mutate and combine them, and selects by downstream model performance, combining LLM domain knowledge with evolutionary search over a non fixed search space ([arxiv 2503.14434](https://arxiv.org/abs/2503.14434), [code](https://github.com/nikhilsab/LLMFE)).

What transfers: the framing that the LLM's job is search guidance, not oracle. LLM-FE keeps a memory of what worked and evolves it, which generalizes the CAAFE/OCTree loop. For Hearth this matters at the level of the entity discovery maintenance job you described: when new sensors appear, you do not re run the whole feature search; you mutate the existing accepted feature spec with the new entities as additional raw material, which is exactly an evolutionary step over the prior population.

What does not transfer: full evolutionary search is many evaluations, which is many model fits and many LLM calls. On a homelab box with seconds to minutes per RF fit and a BYO token budget, you cannot afford a real population based search. Judgment: take the "mutate the prior accepted spec" idea, drop the population size; effectively LLM-FE with population 1 and a tiny number of generations.

### REFEAT (2025)

What it is: argues LLMs default to simple, repetitive feature transformations, and steers them with multiple reasoning mode meta prompts selected by a multi armed bandit that focuses on whichever reasoning lens is yielding validation gains. It is benchmarked against AutoFeat, OpenFE, CAAFE, FeatLLM, and OCTree and reports better accuracy, lower feature complexity, and higher semantic diversity ([emergentmind topic](https://www.emergentmind.com/topics/llm-powered-feature-engineering-5a03ad82-3abb-40c2-aa72-61fc4f51ac1a)).

What transfers: the diagnosis (LLMs are lazy and repetitive in feature proposals) is directly relevant; the practical takeaway for Hearth is to give the LLM a small set of named reasoning lenses in the prompt (for example: "temporal", "cross room co occurrence", "rate of change", "absence and silence") so it does not just propose ten variants of the same threshold. You do not need the bandit machinery; you need the multi lens prompt structure (used in Step 3 prompt design).

What does not transfer: the bandit controller is over engineering at Hearth's scale; with one home and a handful of activity classes, the validation signal per reasoning mode is too noisy to run a bandit over. Manual lens enumeration in the prompt is enough.

### Non LLM baselines: AutoFeat and OpenFE

AutoFeat ([arxiv 1901.07329](https://arxiv.org/pdf/1901.07329), [code](https://github.com/cod3licious/autofeat)): generates a large pool of nonlinear feature transformations then runs multi step selection down to a small robust set for a linear model, keeping interpretability. OpenFE ([PMLR v202 zhang23ay](https://proceedings.mlr.press/v202/zhang23ay/zhang23ay.pdf), [code](https://github.com/IIIS-Li-Group/OpenFE)): expand and reduce; enumerate unary and binary operator combinations up to a small order, then prune with a LightGBM based importance attribution and a bandit resource allocator. Both are expert level automated FE without any LLM.

What transfers: these are the honest control group. The crucial fact for your design is that AutoFeat and OpenFE work purely combinatorially: they have zero semantic understanding. They will happily multiply `zigbee_coordinator_chip_temp` by `bedroom_co2` if it correlates in sample. Hearth's entity funnel and the LLM's semantic pruning exist precisely to stop that. So the right architecture is not LLM versus AutoFE; it is LLM for semantic relevance and naming (which columns are worth combining and why) followed by an OpenFE style cheap combinatorial expansion within the LLM approved set. The LLM shrinks the search space semantically; a combinatorial method can then exhaust the small space safely.

What does not transfer: running AutoFeat or OpenFE over Hearth's full raw entity set is the spurious correlation trap RESEARCH.md already names (Grinsztajn 2022 robustness to uninformative features does not extend to spurious ones at ~10 labels). Judgment: never run unconstrained combinatorial FE on the raw entity space; only inside the LLM approved, role typed subset.

### Summary verdict for Part A

| Method | LLM in inference loop | Executable output | Iterative feedback | Take for Hearth |
|---|---|---|---|---|
| CAAFE | No | Python code | Yes (val gated) | The core pattern: emit transform + rationale, accept/reject on validation. Replace Python with a whitelisted grammar. |
| FeatLLM | No | Parsed rules | No | Validation that pattern 1 works few shot; do not over generate binary rules (RF makes them redundant). |
| OCTree | No | Rules | Yes (tree serialized as feedback) | Serialize the trained model's importances/confusion back into the maintenance prompt. |
| LLM-FE | No | Feature programs | Yes (evolutionary) | "Mutate the prior accepted spec when new sensors arrive." Population 1, few generations. |
| REFEAT | No | Features | Yes (bandit over lenses) | Multi lens prompt structure; skip the bandit. |
| AutoFeat | No | Transforms | Selection only | Cheap combinatorial expansion, but only inside the LLM approved subset. |
| OpenFE | No | Transforms | Bandit pruning | Same; expand and reduce within the safe set. |

The whole of Part A points one direction: design time LLM, executable and validated, with feedback from the trained model. That is Hearth's chosen architecture, and it is the mainstream of the tabular FE field.

## Part B: LLMs for smart home and sensor HAR (mostly pattern 2)

This literature is where Hearth's domain lives, but most of it makes the opposite architectural choice to Hearth, so the lessons are largely cautionary or design time only.

### ADL-LLM (Civitarese et al., 2024/2025)

What it is: raw sensor data is converted to a textual representation and an LLM performs zero shot (or few shot) activity recognition per window, with a regex extracting the activity label from the LLM's text output. Reported weighted F1 up to 0.94 on MARBLE and 0.80 on UCI ADL ([arxiv 2407.01238](https://arxiv.org/abs/2407.01238), [ACM TIST 3725856](https://dl.acm.org/doi/full/10.1145/3725856)).

What transfers: the input representation. ADL-LLM's "serialize a window of sensor events into compact text the LLM can read" is exactly the artifact Hearth already builds for `annotate_windows` (the window summary: "Tue 21:30, sofa 85 percent, media playing, kitchen silent"). It validates that an LLM can label such summaries well. So for design time weak labeling (which Hearth does) this is direct support.

What does not transfer: ADL-LLM is pattern 2. It calls the LLM for every window at recognition time. That is precisely the cost model you reject. Hearth's use of the same input representation is confined to onboarding weak labeling (a one time batch), not steady state recognition. Judgment: ADL-LLM is the reference for Hearth's `annotate_windows` design time labeler, and a direct illustration of the inference cost Hearth avoids by distilling to an RF.

### ZARA (2025/2026)

What it is: Zero shot / training free motion time series reasoning via evidence grounded LLM agents. Its standout component is an automatically derived pairwise feature knowledge base: offline statistical profiling distills, for every pair of activities, the discriminative feature statistics that separate them, turning implicit signal characteristics into verifiable linguistic priors. A retrieval module surfaces relevant evidence and a hierarchical agent selects features and explains predictions ([arxiv 2508.04038](https://arxiv.org/abs/2508.04038), [code](https://github.com/cruiseresearchgroup/ZARA)).

What transfers: the pairwise discriminative feature knowledge base is the single most important idea in Part B for Hearth, and it is a design time idea. The notion is: for each pair of activities the system struggles to separate (read off Hearth's confusion matrix), compute which features are statistically discriminative for that pair, and feed that to the LLM as grounded evidence for proposing a new separating feature. This is a precise, sourced mechanism for the feedback loop you want (confusion matrix to LLM to new feature). It also matches your reliability aim: the same statistical profiling that finds discriminative features can flag features that discriminate nothing (low information sensors).

What does not transfer: ZARA's agentic, retrieval driven, per inference reasoning is again pattern 2 at recognition time. Hearth takes ZARA's offline statistical profiling and pairwise framing into the design time maintenance pass, and drops the inference time agent.

### SensorLLM (2024)

What it is: a two stage framework that aligns an LLM with motion sensor time series (stage 1: align sensor channels to trend descriptions with special boundary tokens; stage 2: task aware tuning for HAR), reaching state of the art on standard HAR datasets, and explicitly noting that text tokenizers handle raw numbers badly ([arxiv 2410.10624](https://arxiv.org/abs/2410.10624)).

What transfers: mostly a warning, plus one design nugget. The warning: LLMs are bad at raw numeric time series because tokenizers shred numbers. This is direct support for Hearth's existing choice to send the LLM summary statistics and trend descriptions, never raw series (and for your new decision to send aggregate stats rather than raw values). The nugget: SensorLLM's "describe the trend, do not dump the numbers" is exactly the right encoding for both the entity catalog and the window summaries Hearth sends.

What does not transfer: SensorLLM fine tunes an LLM to be the classifier (heavy, GPU, pattern 2). Completely out of scope for a local first homelab product. It is the anti pattern Hearth's RF plus design time LLM exists to avoid.

### AgentSense (AAAI 2026)

What it is: a virtual data generation pipeline where LLM driven embodied agents live out generated daily routines in a simulated home (extended VirtualHome with virtual ambient sensors: motion, appliance door, device activation), producing labeled synthetic HAR data to address dataset scarcity ([arxiv 2506.11773](https://arxiv.org/abs/2506.11773), [code](https://github.com/ZikangLeng/AgentSense)).

What transfers: a concept for the cold start, not the steady state. AgentSense's idea is to manufacture labeled data when real labels are scarce. For Hearth this maps to a possible (post v1) capability: use an LLM to synthesize plausible labeled window summaries for a user's declared activities and sensor set, as additional bootstrap signal before real confirmations arrive. It is adjacent to Hearth's existing bootstrap rules and LLM weak labels.

What does not transfer: the heavy simulation stack (VirtualHome) is not something Hearth would embed. And synthetic data carries a distribution shift risk: a simulated routine is not this home's routine. Judgment: interesting for cold start augmentation, but lower priority than the feature architect work, and it must be clearly tiered below real labels (same discipline as the existing provenance tiers).

### DomusFM (2026) and the foundation model direction

What it is: a lightweight foundation model for smart home sensor data, pretrained for ADL recognition and next k event prediction, under 500 MB, about 10 ms inference on edge devices, explicitly motivated by keeping data local and avoiding recurring API cost and latency ([arxiv 2602.01910](https://arxiv.org/html/2602.01910v1)).

What transfers: DomusFM is essentially the published, smart home specific version of Hearth's own HEPA bet (RESEARCH.md section 4): a small self supervised model pretrained on the unlabeled stream, served locally, free at inference, privacy preserving. It is strong independent validation that the local foundation model path is real and that edge inference cost is negligible. If you ever build the Embedder seam (`ports.py` Embedder Protocol), DomusFM is the closest published reference architecture.

What does not transfer (yet): it is a 2026 research artifact, not a dependency you can ship. It belongs exactly where RESEARCH.md puts HEPA: a feature flagged Phase 4 bet behind the Embedder port, not part of the v1 LLM layer. It does not change the LLM design; it complements it (the LLM understands names, a DomusFM style embedder understands signals, per RESEARCH.md section 4b).

### Cumin et al., Knowledge Distillation for LLM based HAR in homes (Jan 2026)

What it is: shows LLM HAR performance scales with model size, then uses knowledge distillation to fine tune a small LLM on HAR reasoning examples generated by a large LLM, reaching nearly the large model's performance with about 50x fewer parameters ([arxiv 2601.07469](https://arxiv.org/abs/2601.07469)).

What transfers: this is the bridge between pattern 2 and Hearth's cost model, and worth understanding precisely. Cumin's distillation keeps an LLM in the inference loop but makes it small enough to run locally and cheaply. That is a different cost solution than Hearth's (Hearth distills the LLM's knowledge into an RF and removes the LLM entirely from inference). Both are valid. Cumin matters as a contingency: if RF on recipe features ever hits an accuracy ceiling that a reasoning model clears, a distilled small local LLM is the documented fallback, and it stays within "no recurring API cost". Judgment: keep RF primary (cheapest, interpretable, already built); note distillation to a small local model as the escalation path if accuracy demands it, parallel to the HEPA bet.

### Summary verdict for Part B

| Method | Pattern | Cost at inference | What Hearth takes |
|---|---|---|---|
| ADL-LLM | 2 (LLM recognizes) | Per window API | The window summary text encoding, for design time weak labeling only. |
| ZARA | 2 (agent at inference) | Per window | The offline pairwise discriminative feature knowledge base, moved to the design time maintenance pass. |
| SensorLLM | 2 (LLM is the model) | GPU, heavy | A warning (LLMs shred raw numbers) confirming the "send stats and trends, not raw series" rule. |
| AgentSense | data generation | Offline | Optional cold start synthetic labels, tiered below real labels. Post v1. |
| DomusFM | local foundation model | ~10 ms local | Validation of the HEPA/Embedder bet; a reference for Phase 4, not v1. |
| Cumin distillation | 2 but small/local | Local, cheap | The escalation path if RF ceilings out: distill to a small local model. Not v1. |

The single most actionable idea from Part B is ZARA's pairwise discriminative feature knowledge base, used at design time, because it is a concrete, sourced mechanism for both feature proposal and reliability flagging.

## Part C: Time series specifics

The tabular FE methods (Part A) ignore time; the HAR methods (Part B) handle time but mostly at inference. Hearth needs the time discipline from HAR applied to design time FE. Four issues.

### Windowing and segmentation

HAR accuracy is dominated by windowing, more than by model family. The canonical smart home result line (CASAS; Cook and Krishnan, Activity Recognition on Streaming Sensor Data, already cited in Hearth's RESEARCH.md and METHODOLOGY.md) builds features on event counts, dominant sensor, and time since last event, not only window aggregates. Hearth already implements this (the `evt_*` features in `pipeline.py`). The open frontier, which Hearth acknowledges as P4, is fixed windows versus segmentation: activities do not align to a 30 minute grid, and transitions are where models err. What transfers from the literature: change point detection on the feature stream (for example PELT/ruptures) to propose segment boundaries is the standard next step, and it improves both recognition and cluster quality.

Mapping to Hearth: windowing must become a first class lever (Step 4 treats it as such), and the LLM can reason about window length per role at design time (a step counter needs hours, a motion sensor needs minutes; Hearth already encodes `window_min` per role in the registry, but it is a code constant, not an LLM or user decision).

### Temporal leakage

This is the most important correctness point in the whole brief, and it is where naive application of the tabular FE papers would silently break Hearth. CAAFE, OpenFE and AutoFeat all assume i.i.d. rows and select features by random cross validation. Sensor windows are heavily autocorrelated: a window at 21:00 and 21:05 (5 minute stride) are nearly identical. Random k fold CV puts adjacent, near duplicate windows in both train and test, which inflates the validation score the FE loop optimizes, so the LLM gets rewarded for features that only look good under leakage. Hearth already avoids this in two places (the trainer uses a temporal holdout, `feats.index < cutoff`, and `tune_hyperparams` uses `TimeSeriesSplit`, never shuffled). The design implication for Step 3 is that the feature accept/reject loop must use the same temporal or blocked split, otherwise the LLM feature architect optimizes a leaked metric. This is the bridge between Part A's val gating and Hearth's existing temporal discipline, and it is non negotiable (expanded in Step 4's validation section).

### RAG for domain temporal patterns

ZARA's knowledge base is effectively retrieval augmented generation over discriminative statistics. The general idea that transfers: instead of stuffing everything into one prompt, retrieve the relevant evidence per decision. For Hearth, "retrieval" is cheap and local: given a target activity pair to separate, retrieve the few entities whose stats are most discriminative for it and put only those in the prompt. This both controls context size (Part D of Step 3) and grounds the LLM in this home's actual statistics rather than priors. You do not need a vector database; the retrieval is a statistical ranking over the entity catalog.

### Removing the LLM from the inference loop: distillation, quantization, pruning

The literature offers three ways to keep LLM quality without LLM inference cost: distillation (Cumin: train a small student on a big teacher's reasoning), quantization and pruning (shrink the model to run locally), and the Hearth way, which is the most aggressive form of distillation: distill the LLM's semantic knowledge into a feature specification and a set of labels, then train a non LLM model (RF) that contains all of that knowledge implicitly. Quantization and pruning are about making an LLM cheaper to run; Hearth's choice removes the LLM from the runtime entirely, which is strictly cheaper than any quantized model. The relevant judgment: Hearth's architecture is the extreme and correct end of the "remove the LLM from inference" spectrum for a cost sensitive local product. Distillation to a small local LLM (Cumin) is the only fallback worth keeping in mind, and only if the RF ceilings out.

## Part D: Where the LLM should sit (recommendation)

Recommendation: the LLM sits at design time and at maintenance time, and never at inference time. Concretely, three touch points, all off the steady state prediction path:

1. Onboarding (one shot): semantic relevance and selection of entities, role assignment, executable feature specification (the CAAFE/FeatLLM/OCTree pattern), draft labeling rules, taxonomy, and optionally batched weak labels (the ADL-LLM input encoding). This is where the bulk of token spend happens, once.
2. Maintenance (scheduled, gated by user approval): the entity discovery job you described. A cheap, LLM free scan detects new HA entities daily or hourly; if new entities appear, the user is asked whether to integrate them; only on approval does the LLM analyze the new entities and revise the feature spec, then a background retrain plus the existing promotion gate decides whether to go live. Plus a periodic feedback pass that reads the trained model's confusion matrix and importances (OCTree style) and the pairwise discriminative statistics (ZARA style) to propose separating features for the classes the model confuses.
3. Insight (on demand, optional): plain language explanations of bindings, cluster naming hints, and methodology narration. Negligible cost, user triggered.

Why this is correct for Hearth specifically:

- Cost model: it is the only placement consistent with "spend once, run free." Patterns that put the LLM at inference (ADL-LLM, ZARA, SensorLLM) burn tokens per prediction forever; even the distilled small model (Cumin) still runs a model per window. Hearth's RF runs for compute cost only.
- The literature agrees for the tabular half: every tabular FE method surveyed (CAAFE, FeatLLM, OCTree, LLM-FE, REFEAT) places the LLM at design time and a cheap model at inference. Hearth is a tabular learning problem after windowing, so this is the mainstream choice, not a compromise.
- It preserves interpretability and the glass box promise: an RF with SHAP is auditable; an LLM recognizer is not. The design time placement keeps the runtime fully inspectable.
- It matches the privacy contract: design time the LLM sees metadata and aggregate statistics (and, per your new decision, only with explicit user consent via a yes/no toggle whose implications are spelled out); it never sees raw history, and at inference it sees nothing because it is not there.
- It is robust to LLM failure: because the LLM only ever proposes (validated, human approved) and the system degrades to a heuristic floor, an LLM outage, a rate limit, or a hallucination cannot break steady state prediction. A pattern 2 system goes blind when the LLM is unavailable.

The one contingency to keep documented: if RF on recipe and LLM authored features hits an accuracy ceiling on real homes that a reasoning model clears, the escalation path is not "put GPT in the loop" but either the HEPA/DomusFM local embedder bet (RESEARCH.md section 4) or Cumin style distillation to a small local model. Both stay within "no recurring API cost." Neither is v1.

This recommendation is exactly the architecture Hearth already committed to (the LLM is onboarding only today). Step 2's contribution is to show it is also what the field's tabular FE mainstream does, to import the specific mechanisms worth adding (executable spec, model to LLM feedback, pairwise discriminative knowledge base, multi lens prompting, temporal leakage discipline), and to name the inference time HAR LLM literature as the cost trap to avoid.

## Sources

- CAAFE: Hollmann, Müller, Hutter, NeurIPS 2023. [arxiv 2305.03403](https://arxiv.org/abs/2305.03403), [code](https://github.com/noahho/CAAFE)
- FeatLLM: Han, Yoon, Arik, Pfister, ICML 2024. [PMLR v235 han24f](https://proceedings.mlr.press/v235/han24f.html), [arxiv 2404.09491](https://arxiv.org/pdf/2404.09491)
- OCTree: Nam et al., NeurIPS 2024. [arxiv 2406.08527](https://arxiv.org/abs/2406.08527), [code](https://github.com/jaehyun513/OCTree)
- LLM-FE: Abhyankar, Shojaee, Reddy, 2025. [arxiv 2503.14434](https://arxiv.org/abs/2503.14434), [code](https://github.com/nikhilsab/LLMFE)
- REFEAT (LLM powered feature engineering, reasoning lenses + bandit). [emergentmind topic](https://www.emergentmind.com/topics/llm-powered-feature-engineering-5a03ad82-3abb-40c2-aa72-61fc4f51ac1a)
- AutoFeat: Horn, Pack, Rieger, 2019. [arxiv 1901.07329](https://arxiv.org/pdf/1901.07329), [code](https://github.com/cod3licious/autofeat)
- OpenFE: Zhang et al., ICML 2023. [PMLR v202 zhang23ay](https://proceedings.mlr.press/v202/zhang23ay/zhang23ay.pdf), [code](https://github.com/IIIS-Li-Group/OpenFE)
- ADL-LLM: Civitarese et al. (LLMs are Zero Shot Recognizers for ADL), 2024/2025. [arxiv 2407.01238](https://arxiv.org/abs/2407.01238), [ACM TIST 3725856](https://dl.acm.org/doi/full/10.1145/3725856)
- ZARA: Zero shot / training free motion time series reasoning, 2025/2026. [arxiv 2508.04038](https://arxiv.org/abs/2508.04038), [code](https://github.com/cruiseresearchgroup/ZARA)
- SensorLLM: 2024. [arxiv 2410.10624](https://arxiv.org/abs/2410.10624)
- AgentSense: AAAI 2026. [arxiv 2506.11773](https://arxiv.org/abs/2506.11773), [code](https://github.com/ZikangLeng/AgentSense)
- DomusFM: A Foundation Model for Smart Home Sensor Data, 2026. [arxiv 2602.01910](https://arxiv.org/html/2602.01910v1)
- Cumin et al., Knowledge Distillation for LLM Based Human Activity Recognition in Homes, Jan 2026. [arxiv 2601.07469](https://arxiv.org/abs/2601.07469)
- Survey: LLMs for Wearable Sensor Based HAR, 2024. [arxiv 2407.07196](https://arxiv.org/abs/2407.07196)

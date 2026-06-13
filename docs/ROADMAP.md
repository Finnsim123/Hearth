# Hearth — Roadmap

> Part of the [Hearth](../README.md) docs · design language in [DESIGN.md](DESIGN.md)

Each phase ends in something a stranger could run. Don't start phase N+1 with
phase N's acceptance unmet.

## Phase 0 — Skeleton boots (this repo, now)
Docker compose brings up InfluxDB + backend; `/api/health` green; UI shell
renders; CI runs lint + tests on the stubs.
**Accept:** `docker compose up` → healthy stack on a clean machine.

## Phase 1 — Data pipeline end-to-end (pillar 1) — ✅ backend implemented
Done: HA WebSocket ingest (+ gap-fill), InfluxDB store (3 buckets), SQLite repo
(+ encrypted connections, users), security.py, role recipes + composites-as-data
AST + window builder + semantic imputation, binding suggestion heuristics,
history importer (both HA→Influx schemas), scheduler + API wiring, 27 unit
tests. Remaining for acceptance: Sensors page freshness UI, live validation
against a real HA + InfluxDB (run on the homelab; sandbox has neither).
**Accept:** for a configured home, features for the last hour are queryable in
Influx and visible in the UI within 5 min of events happening.

## Phase 2 — Model + training (pillar 2) — ✅ ML core implemented
Done: provenance label merge, RF estimator (+SHAP normalization), honest
evaluation (confirmed-vs-bootstrap accuracy, Wilson CIs, PSI drift), trainer +
registry + CI-aware promotion gate + rollback, inference with BOOTSTRAP-RULES
FALLBACK (rules-v0 → day-one ribbon), hysteresis smoothing, ε-greedy asking
policy with budgets/quiet-hours/cooldowns, dynamic question phrasing, weekly
training cron + 5-min inference job, models/activities/rules API. 43 tests.
Remaining: HA integration entity platforms (HA-side code), Models UI page,
MQTT publisher, live validation on a real home.

### original scope (for reference)
Taxonomy + rule engine (bootstrap labels); trainer + registry + promotion gate;
metrics report incl. confirmed/bootstrap split, CIs, SHAP; Models page complete;
inference job writes predictions; **HA integration v1** (config flow host+token,
per-member device, push over WS) — MQTT publisher as the alternate channel.
Optional **LLM onboarding advisor** (OpenRouter/OpenAI-compatible, ADR-12)
upgrading the heuristic suggesters in the wizard.
**Accept:** "Train now" in the UI produces a registered, promoted model whose
predictions show up as `sensor.hearth_<person>_activity` in HA via the
integration.

## Phase 3 — Feedback loop (pillar 3) — ✅ implemented
Done (partly during the notification work): questions service + ε-greedy
asking policy (budgets, quiet hours, cooldowns), dynamic phrasing, integration-
handled action capture (zero YAML), Inbox page (one-tap answers, skip, bulk
labeler), dashboard ribbon tap-to-correct (source=ribbon), label overlay into
training, weekly retrain + question expiry jobs. 49 tests.
Remaining: drift panel UI (Models page), live shakedown.

### original scope (for reference)
Questions service + asking policy (ε-greedy, budgets, cooldowns); HA blueprint
webhook for notification actions; Inbox + bulk labeler; labels overlay training;
weekly retrain schedule; drift panel.
**Accept:** a tapped phone notification or Inbox answer becomes a `confirmed`
label, and the next retrain consumes it; confirmed-accuracy is the headline
number on the dashboard.

## Phase 4 — Discovery + research bets
**Core: DONE (June 2026).** HDBSCAN pattern cards (sklearn, per person, weekly
Sat 04:00 + on-demand) → name/merge/dismiss on the Patterns page → naming emits
provenance=discovered labels for all member windows AND drafts a disabled Rule
(review + enable on the Activities page). Confirmed windows excluded from
clustering; re-runs replace the 'new' pile and dedupe against handled cards by
signature overlap; optional LLM "AI thinks: <slug>" hint per card.
**Accept status:** discovery activates at ≥120 windows (~60 h) so the 72 h bar
is reachable; "≥3 sensible cards" and "naming measurably improves the next
training run" still need verification on a real fresh install — check the
Models page confirmed-accuracy before/after naming.

### Research bets — open (deliberately deferred)
- **Data-driven feature admission** (replaces the hand-written binding gate as
  labels accumulate). The setup heuristic + blocklist is a PRIOR for the
  cold-start, scarce-label regime: with ~10 confirmed labels a junk feature
  spuriously correlates with the target (the alarm-clock failure), so a tight
  gate is correct EARLY. It is wrong as a permanent law — some excluded
  entities (network throughput, router device counts, specific power sensors)
  carry real behavioural signal. Plan: once a person crosses ~300 confirmed
  labels (spurious-correlation risk has dropped), periodically re-admit
  borderline-excluded entities as CUSTOM bindings, retrain, and KEEP only the
  ones the importance chart earns — evidence replaces assumption. Gate the
  experiment on label count + a hold-out accuracy check so it can't regress the
  live model. Until then the appeal path (manual bind + LLM-reasoned override)
  covers the long tail.
- Change-point segmentation experiment (P4)
- HEPA-style embedder behind the `Embedder` port (feature-flagged): pretrain on
  the home's unlabeled stream, side-by-side vs RF in the registry,
  embedding-space clustering. The port and an `EmbeddingEstimator` seam now
  exist (identity passthrough, selectable as the `embedding` family); the
  self-supervised encoder itself (`adapters/hepa_embedder.py`) is the remaining
  work — the JEPA / world-model bet (RESEARCH.md §World models).

## AI feature layer + ML depth — ✅ implemented (June 2026)
Beyond the original three pillars, shipped:
- **Feature architect (optional LLM):** entity selection, information tiers, an
  executable feature spec from a safe transform whitelist, cross-entity
  composites, and reliability flagging — with a pre-run cost estimate and an
  explicit aggregate-stats consent choice. Heuristic floor + role recipes when no
  key; a deterministic reliability pass runs either way.
- **Detect-then-ask sensor lifecycle:** new HA entities are staged for approval,
  not auto-added; approval runs a scoped re-analysis + background retrain, gated
  by the promotion gate.
- **Model-to-LLM feedback loop:** discriminative statistics per confused class
  pair feed a minimal spec revision, gated by the promotion gate / a
  confirmed-label floor.
- **Selectable model family:** random forest (default), gradient-boosted,
  logistic, and an embedding head behind the enriched `Estimator` port.
- **Cold-start honesty:** a model is *provisional* until enough confirmed labels
  validate it (never presented as validated on circular bootstrap signal).
- **Abstain state:** below a confidence threshold the published state is
  `unknown`, so automations don't act on a shaky guess.
- **Levers as data + UI:** training / asking / output policies and the above are
  editable in Settings; the Sensors page shows pending approvals, the feature
  spec and reliability flags; the Models page shows per-version trend + compare.

## Phase 5 — Ship it
Hardening (authn, token encryption audit, backup/restore), docs site, example
configs, HA add-on packaging (thin wrapper, Frigate-style), community recipe
sharing format. Optional: Grafana dashboards pack.
**Accept:** a Reddit/r-homeassistant stranger installs without filing an issue.

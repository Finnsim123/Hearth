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
HDBSCAN pattern cards → name/merge/dismiss → discovered labels + drafted rules.
Change-point segmentation experiment (P4). HEPA-style embedder behind the
`Embedder` port (feature-flagged): pretrain on the home's unlabeled stream,
side-by-side vs RF in the registry; embedding-space clustering for patterns.
**Accept:** a new user with zero labels gets ≥3 sensible pattern cards after
72 h of recording; naming one card measurably improves next training run.

## Phase 5 — Ship it
Hardening (authn, token encryption audit, backup/restore), docs site, example
configs, HA add-on packaging (thin wrapper, Frigate-style), community recipe
sharing format. Optional: Grafana dashboards pack.
**Accept:** a Reddit/r-homeassistant stranger installs without filing an issue.

## Migration: har-homelab → Hearth
1. Run Hearth alongside the prototype (different buckets — no interference).
2. History importer ingests the existing `homeassistant` bucket.
3. Port confirmed labels (`har_labels`) with provenance `confirmed`.
4. Bindings replicate the prototype's sensor map; recipes already ported.
5. Parity check: Hearth's RF vs prototype metrics on the same weeks.
6. Decommission prototype cron; keep it archived.

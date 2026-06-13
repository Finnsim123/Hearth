# Hearth — Web UI Specification

> Part of the [Hearth](../README.md) docs · design language in [DESIGN.md](DESIGN.md)

React + TypeScript SPA at `:8420`, served by the backend. REST for CRUD,
WebSocket (`/ws`) for live predictions, ingest status, and training logs.
Design language: dark-first dashboard, dense but calm; every ML number links to
an explanation of how it was computed.

## Navigation

```
⌂ Dashboard · ✉ Inbox · ⊞ Activities · ✦ Patterns · ⚙ Models · ⌁ Sensors · ☰ Logs · ⚙ Settings
```

## 1. Onboarding wizard (first boot, resumable)

**Implemented** in `frontend/src/onboarding/` (`Wizard.tsx` + `ui.tsx`); backend
calls all wired to the real API. UX pattern, identical on every step: progress
bar (ember fill) -> "Step n of 10" label -> title -> one-paragraph explainer in
plain language -> fields with inline hints ("where do I find this") and inline
validation -> a "what's happening" callout explaining what Hearth does with the
input -> footer with Back / Continue (+ prominent Skip on optional steps).
Continue stays disabled until the step is genuinely complete (e.g. connection
test green). State persists in localStorage so a closed tab resumes mid-flow;
passwords are never persisted.

1. **Create your account** — first boot has no users: set admin email +
   password (argon2id, see docs/SECURITY.md). Additional accounts later in
   Settings → Users.
2. **Connect Home Assistant** — URL + long-lived token; live validation; shows
   discovered entity count.
3. **Time-series database** — explicit fork:
   - "I already run InfluxDB" → URL + org + token form, then a STAGED check
     (real endpoint /api/influx/inspect): instance reachable ✓ → token
     accepted ✓ → N buckets found ✓. Then a source-bucket picker for history
     import (auto-suggests the busiest bucket, shows measurements · points/24h
     · history-since per bucket, "no import" opt-out). Hearth's own three
     buckets are created automatically — never asked for.
   - "Set it up for me" → uses the InfluxDB bundled with the stack, which runs
     alongside Hearth. The wizard auto-connects using the generated admin token
     (nothing to enter) and waits briefly while InfluxDB finishes starting. Pick
     this and you're done on this step.
4. **MQTT** (optional) — the open output channel. Enter a broker host/port (and
   credentials if any) and Hearth publishes each person's activity and confidence
   as retained Home-Assistant discovery entities, which any hub that speaks
   HA-style MQTT discovery picks up (Home Assistant, Homey, Node-RED, openHAB).
   Use it when you're not on Home Assistant or prefer broker wiring. If you'll use
   the Hearth HA integration (step 9), skip this — they do the same job and the
   integration needs no broker.
5. **Household** — create the family: add any number of members (adults, kids,
   roommates), each with optional `person.*` entity, optional notify service,
   ask budget and quiet hours. Members without a phone (`has_device=false`,
   typical for kids) are never notified — their activity is labeled by other
   members or via the Inbox. Every enabled member gets their own model;
   members can be disabled (guest room, infant) without deleting history.
6. **Inventory** — automatic: Hearth pulls every entity (states + entity/area
   registry) and computes per-entity stats from available history (DATA_MODEL
   §4). Shows a summary ("214 entities, 31 with useful signal, 14 d of
   history") with a **Download inventory.json** button so users can inspect
   exactly what an LLM would see.
6b. **AI assist (optional)** — paste an OpenRouter / OpenAI-compatible API key
   to let an LLM act as a *feature architect* over the inventory: it selects
   which entities matter, assigns each an information tier, designs an executable
   feature spec (from a safe transform whitelist) plus cross-sensor composites,
   drafts taxonomy and rules, and **flags unreliable sensors** — returning
   clarifying questions where ambiguous. An explicit **yes/no choice** governs
   whether the model may see aggregate per-sensor statistics (default off =
   metadata only), and an **estimated one-time cost** is shown before any run.
   "Skip — use built-in heuristics" is equally prominent. Key stored encrypted,
   reusable later for re-analysis and cluster-naming hints, removable in Settings.
   Predictions run fully locally; the LLM is used only for setup and re-analysis.
7. **Sensors** — table of HA entities with *suggested* role/room/person
   (heuristics, or LLM proposals with a reason per row — badge shows which);
   user confirms/edits; unbound entities are simply ignored. One-click
   "import history" if an existing HA→Influx bucket is detected.
8. **Activities** — starter taxonomy presets (minimal: sleeping/away/home;
   standard: +cooking/eating/movie/working; custom), or the LLM's inventory-
   tailored proposal — fully editable later.
9. **Connect output to HA** — one-click, in the user's own HA: buttons
   deep-link via `{ha_url}/_my_redirect/hacs_repository?...` (opens HACS's
   add-repo dialog) and `/_my_redirect/config_flow_start?domain=hearth`
   (opens the add-integration flow) — same endpoints the my.home-assistant.io
   buttons use, but pointed at the HA URL from step 2. Because the backend
   announces `_hearth._tcp.local.` over mDNS, the config flow arrives with
   the host pre-filled; the user only pastes the token generated here (shown
   once). Manual fallback instructions in a callout; MQTT remains the
   alternative channel.
10. **Done** — ingest starts; the wizard hands off to the live Welcome screen
   below.

### 1a. Welcome hand-off (onboarding/Welcome.tsx)

A full-screen moment between the wizard and the dashboard that introduces the
buddy (Ember) and *shows* the pipeline running on this home's real data — no
faked numbers. It polls `GET /api/buddy` for the current phase and `GET /api/flow`
for live counts, and animates an entity strip over the sensors Hearth actually
bound (`GET /api/bindings`). Two arcs, chosen by whether history was imported
(flag in `localStorage['hearth.welcome']`):

- **Fast-track** (imported history): the stepper lights up live as the phase
  advances — scanning your home → reading with AI (only if a key is set) →
  building features → learning your routines → finding patterns — finishing
  with the first model live and CTAs to name patterns / answer questions.
- **Fresh** (no history): scanning completes, the later stages show "starts as
  your data arrives", and the copy explains the few-days wait.

A "Go to my dashboard" button is always present (the work continues in the
background); reaching the screen later still reflects current state. Clearing
the localStorage flag on exit makes it a one-time moment.

## 2. Dashboard (implemented in pages/Dashboard.tsx)

Answers one question in two seconds: "what does Hearth think is happening —
and can I trust it?" Two modes:

**Cold start** (no predictions yet): a single "Hearth is learning your home"
journey card — day counter, events/24 h, sensors bound, progress bar to day 7,
three milestone rows (recording → first patterns → first model) that tick off
as they fire. Paired with milestone PHONE NOTIFICATIONS (domain/milestones.py):
"recording started", "first patterns — come name them", "Hearth is live ✨".
The closing wizard screen sets the expectation: go live your life, we'll ping you.

**Steady state**:
- **Hero: avatar scene cards per person** — the member's avatar (photo or
  preset disc) badged onto the current activity's icon tile in its palette
  color: you SEE Alice on the bed, not just text. Confidence micro-bar,
  since-when, "because" SHAP strip (Phase 2).
- **Today ribbon** per person (smoothed, opacity = confidence), tap a segment
  to correct → highest-volume labeling surface. Badged "rule-based until
  trained" while inference runs on bootstrap rules (model_version rules-*).
- **Needs you** (max 3, hidden when empty): top open questions answered inline.
- **Trust strip** (Phase 2): confirmed accuracy ± CI, labels this week, next
  retrain, drift dot.
- **System pulse** footer: database / ingest / bindings dots — quiet when
  green, loud when red.

## 3. Inbox (the feedback loop surface)

- Queue of open questions (uncertain windows, random exploration asks) and
  recent low-confidence segments. Each card: time range, sensor summary,
  predicted activity + confidence, one-tap activity buttons (taxonomy-driven),
  "split window" and "skip".
- **Bulk labeler**: calendar/timeline picker → select range → assign activity
  (provenance `confirmed`, flagged `bulk`).
- Notification deep links land here (`/inbox?q=<question_id>`).

## 4. Activities

- Taxonomy editor: two-level tree, CRUD, icon/color, per-person enable,
  and a **notification phrase** per activity ("watching a movie") used by
  the dynamic question engine (labeling/phrasing.py).
- **Rules tab** per activity: list of labeling rules (predicate builder UI —
  feature dropdown, operator, threshold, AND/OR groups), priority ordering,
  origin badge (user / discovered), live "matches last 7 d: 23 windows" preview.
- Class health: confirmed-label count per class, warning when a class is
  untrainable (<min samples).

## 5. Patterns (discovery)

- Grid of cluster cards: time-of-day histogram, top distinguishing features
  ("sofa ↑ · media playing ↑ · evening"), window count, example windows.
- Actions: **Name it** (pick/create activity → windows become `discovered`
  labels + drafted rule shown for approval), **Merge** with another card,
  **Dismiss** (noise), **Snooze**.
- Embedding scatter (UMAP) with cluster coloring for the curious.

## 6. Models

- **Registry table**: version, person, algo, feature_set, trained_at, n_train,
  n_confirmed, accuracy_confirmed (with CI), accuracy_bootstrap, and a
  **live / validated / provisional** status badge (provisional until enough
  confirmed labels validate it).
- **Per-model detail** (built): per-class precision/recall/F1, confusion matrix
  heatmap, macro AUC, global feature importances, evidence-tier profile,
  label-provenance breakdown, hyperparameters, feature-set version.
- **History & trend** (built): confirmed-accuracy + AUC across versions (the
  drift-over-time view) plus a compact compare table of every version.
- **Actions**: Train now (per person; streams logs over WS), Promote (promote a
  prior version to roll back).
- **Planned**: ROC curves, calibration plot, per-feature PSI drift panel, and a
  full side-by-side version diff.

## 7. Sensors

- Bindings table: entity, role, room, person, freshness dot (last point age),
  7-d sparkline, enable toggle, and a **reliability** state — live / no-variation
  / no-data plus a *suspect* flag (rarely changes, long gaps) from a
  deterministic stats pass that runs with OR without an LLM. Add-binding flow =
  mini step 5 of wizard.
- **Newly discovered sensors** (detect-then-ask): a card listing sensors found
  since setup, with per-sensor and bulk **Approve / Dismiss**. Approving binds
  them and triggers a scoped AI re-analysis + background retrain; nothing enters
  the model unprompted.
- **AI feature design** panel (collapsible): the active feature spec — kept
  entities with their information tier and reliability, and the executable
  features with the rationale behind each. Absent when the default recipes are
  in use (no LLM).
- Ingest health: WebSocket connection state, events/min, gap-fill runs,
  per-binding last-write.

## 8. Settings

- Connections (HA / MQTT / Influx / LLM advisor) with test buttons and token
  rotation; per-call cost log for the LLM.
- **API tokens** — mint/revoke scoped tokens for the HA integration (and
  future external consumers); plaintext shown once at mint; last-used
  timestamps; masked everywhere else (docs/SECURITY.md).
- **Users** — accounts (admin/member), create/disable, reset password, active
  sessions with revoke; optional link account ↔ household member so inbox
  answers record who confirmed.
- Asking policy: confidence threshold, ε random-ask rate, daily budget,
  quiet hours, cooldowns.
- **AI assistant: data sharing** — the explicit yes/no for sharing aggregate
  stats with the LLM, with the implications of each choice spelled out.
- **Feature engineering power** — conservative (recipes + basic composites) vs
  full (richer transforms).
- **Model family** — random forest / gradient-boosted / logistic / embedding.
- **Commit threshold (abstain)** — confidence below which Hearth publishes
  `unknown`, with an on/off toggle.
- Schedules: window builder cadence, training schedule, discovery schedule.
- Data: retention, export (features/labels CSV), danger zone (reset).
- Appearance: theme — System (default) / Light / Dark (also cyclable from the
  nav); follows docs/DESIGN.md §7.
- About: version, docs links, anonymized-stats opt-in (default off).

## 9. Logs

Recent backend activity, read from an in-memory ring buffer (the last ~2000
records since start) so the operator can see what Hearth is doing without
shelling into the container. Level filter (DEBUG/INFO/WARNING/ERROR), text
search, pause/resume of the 3-second incremental poll, and copy-to-clipboard.
Held in memory only — the container's stdout/journald keeps the durable history.
Backed by `GET /api/logs` (session-only; never exposed to the integration token
scope).

## API surface (consumed by the SPA)

```
POST /api/auth/setup (first boot only)    POST /api/auth/login | /logout
GET  /api/auth/me                         CRUD /api/users (admin)
GET  /api/health                          GET  /api/persons
CRUD /api/connections                     CRUD /api/bindings (+ /suggest)
CRUD /api/activities  /api/rules          GET  /api/predictions?person&range
GET  /api/features/schema                 GET  /api/inbox     POST /api/inbox/{id}/answer
POST /api/labels/bulk                     GET  /api/clusters  POST /api/clusters/{id}/name
GET  /api/models      POST /api/models/train|promote|rollback
GET  /api/system/status                   POST /api/feedback/action   (HA webhook)
WS   /ws                                  (predictions, ingest, training logs)

# AI feature layer + ML levers
GET  /api/bindings/health                 (status + reliability per binding)
GET  /api/sensors/pending                 POST /api/sensors/pending/approve|dismiss
GET  /api/feature-spec                    POST /api/feature-spec/estimate
GET/POST /api/feature-power               GET/POST /api/stats-consent
GET/POST /api/model-family                GET/POST /api/output-policy
```
